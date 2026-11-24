import re
from datetime import UTC,datetime
from fractions import Fraction

from app.errors import RoastError

PERIOD=re.compile(r'\d{4}-(0[1-9]|1[0-2])\Z')
DESTINATIONS=('pack_g','loss_g','hold_g','return_g')
OUTBOUND=('pack_g','loss_g')
IN_STOCK=('hold_g','return_g')


def check_period(period):
    if not isinstance(period,str) or not PERIOD.fullmatch(period):
        raise RoastError('BAD_PERIOD','账期须为 YYYY-MM')
    return period


def parse_price(value):
    try:
        price=Fraction(str(value).strip())
    except (ValueError,ZeroDivisionError):
        raise RoastError('BAD_PRICE','单价须为正的元/kg 数值') from None
    if price<=0:
        raise RoastError('BAD_PRICE','单价须为正的元/kg 数值')
    return price


def disposition(packing):
    slots={}
    for row in packing['outputs'].values():
        if row['status']=='certified' and row['kind'] in ('pack','loss'):
            for origin,value in row['origin_g'].items():
                slots.setdefault(origin,dict.fromkeys(DESTINATIONS,Fraction()))[row['kind']+'_g']+=Fraction(value)
    for rows,key,dest in ((packing['jobs'],'hold_origin_g','hold_g'),(packing['returns'],'remaining_origin_g','return_g')):
        for row in rows.values():
            for origin,value in row[key].items():
                slots.setdefault(origin,dict.fromkeys(DESTINATIONS,Fraction()))[dest]+=Fraction(value)
    return slots


def close_month(reports,period,run,prices,sealed_at):
    origins,booked,pending_out,pending_stock,pending_g={},Fraction(),Fraction(),Fraction(),Fraction()
    for origin,slot in sorted(disposition(reports['packing']).items()):
        used=sum(slot.values())
        row={key:str(value) for key,value in slot.items()}
        row['used_g']=str(used)
        price=prices.get(origin)
        if price is None:
            row.update(unit_price=None,cost=None,pending_g=str(used))
            pending_g+=used
            pending_out+=sum(slot[key] for key in OUTBOUND)
            pending_stock+=sum(slot[key] for key in IN_STOCK)
        else:
            cost=used*price/1000
            row.update(unit_price=str(price),cost=str(cost),pending_g='0')
            booked+=cost
        origins[origin]=row
    totals=dict(used_g=str(sum((Fraction(row['used_g']) for row in origins.values()),Fraction())),
                booked_cost=str(booked),pending_g=str(pending_g),
                pending_outbound_g=str(pending_out),pending_stock_g=str(pending_stock),
                uncertified_g=reports['balance']['warehouse']['pending_g'])
    return dict(period=period,run=run,status='closed',sealed_at=sealed_at,origins=origins,totals=totals)


def supplement(closing,origin,price,booked_at):
    row=closing['origins'][origin]
    outbound=sum((Fraction(row[key]) for key in OUTBOUND),Fraction())
    in_stock=sum((Fraction(row[key]) for key in IN_STOCK),Fraction())
    return dict(period=closing['period'],origin=origin,unit_price=str(price),booked_at=booked_at,
                adjustment=dict(outbound_g=str(outbound),stock_g=str(in_stock),
                                difference=str(outbound*price/1000),inventory=str(in_stock*price/1000)))


def summarize(closing,supplements):
    difference=sum((Fraction(row['adjustment']['difference']) for row in supplements),Fraction())
    inventory=sum((Fraction(row['adjustment']['inventory']) for row in supplements),Fraction())
    covered=sum((Fraction(row['adjustment']['outbound_g'])+Fraction(row['adjustment']['stock_g']) for row in supplements),Fraction())
    remaining=Fraction(closing['totals']['pending_g'])-covered
    return dict(**closing,supplements=supplements,remaining_pending_g=str(remaining),
                adjustments=dict(difference=str(difference),inventory=str(inventory),covered_g=str(covered)))


def close_period(ledger,reports,period,run,prices):
    check_period(period)
    existing=ledger.closing(period)
    if existing is not None:
        if existing['run']!=run:
            raise RoastError('CLOSING_SEALED','该月已按其他归档封账，金额保留原数',409)
        return summarize(existing,ledger.supplements(period)),False
    record=close_month(reports,period,run,prices,datetime.now(UTC).isoformat(timespec='seconds'))
    if ledger.seal_closing(record):
        return summarize(record,[]),True
    stored=ledger.closing(period)
    if stored['run']!=run:
        raise RoastError('CLOSING_SEALED','该月已按其他归档封账，金额保留原数',409)
    return summarize(stored,ledger.supplements(period)),False


def add_supplement(ledger,period,origin,price):
    check_period(period)
    closing=ledger.closing(period)
    if closing is None:
        raise RoastError('NOT_CLOSED','该月尚未关账，不能补价',404)
    row=closing['origins'].get(origin) if isinstance(origin,str) else None
    if row is None:
        raise RoastError('NO_ORIGIN','该产地批不在本月账内',404)
    if Fraction(row['pending_g'])<=0:
        raise RoastError('NOT_PENDING','该产地批无待补重量',422)
    record=supplement(closing,origin,price,datetime.now(UTC).isoformat(timespec='seconds'))
    if ledger.seal_supplement(record):
        return record,True
    stored=ledger.supplement(period,origin)
    if stored['unit_price']!=record['unit_price']:
        raise RoastError('SUPPLEMENT_SEALED','该补价单已按其他单价入账',409)
    return stored,False
