from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

from app.main import create_app
from app.modules.closing.ledger import Ledger
from app.modules.closing.service import add_supplement,close_period
from app.pipeline import compute

PRICES={'BR-08':'80','CO-04':'120'}


def archive(client,sample='dirty'):
    return client.post('/api/runs',json={'sample':sample}).json['id']


def close(client,run,period='2026-11',prices=PRICES):
    return client.post('/api/closing',json={'period':period,'run':run,'prices':prices})


def test_close_books_priced_costs(client):
    run=archive(client)
    response=close(client,run,prices={**PRICES,'ET-01':'96'})
    assert response.status_code==201
    closing=response.json
    assert closing['status']=='closed' and closing['run']==run
    assert closing['totals']['pending_g']=='0' and closing['remaining_pending_g']=='0'
    assert closing['origins']['ET-01']['cost']=='384/5'
    assert Fraction(closing['totals']['booked_cost'])==Fraction(550*80+320*120+800*96,1000)


def test_missing_price_pends_only_its_share(client):
    run=archive(client)
    closing=close(client,run).json
    row=closing['origins']['ET-01']
    assert (row['cost'],row['unit_price'])==(None,None) and row['pending_g']=='800'
    assert (row['pack_g'],row['loss_g'],row['hold_g'],row['return_g'])==('30457/50','10867/250','5924/125','100')
    assert closing['origins']['BR-08']['cost']=='44' and closing['origins']['CO-04']['cost']=='192/5'
    totals=closing['totals']
    assert totals['booked_cost']=='412/5' and totals['pending_g']=='800'
    assert (totals['pending_outbound_g'],totals['pending_stock_g'])==('81576/125','18424/125')
    assert totals['uncertified_g']=='390'


def test_close_idempotent_same_run(client,tmp_path):
    run=archive(client)
    first=close(client,run)
    again=close(client,run)
    assert (first.status_code,again.status_code)==(201,200)
    assert again.json['totals']==first.json['totals'] and again.json['sealed_at']==first.json['sealed_at']
    assert len(list((tmp_path/'ledger').glob('closing-*.json')))==1


def test_close_conflict_other_run_keeps_sealed(client):
    run=archive(client)
    other=archive(client,'clean')
    close(client,run)
    conflict=close(client,other)
    assert (conflict.status_code,conflict.json['code'])==(409,'CLOSING_SEALED')
    assert client.get('/api/closing/2026-11').json['run']==run


def test_supplement_splits_outbound_and_stock(client):
    run=archive(client)
    sealed=close(client,run).json
    response=client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    assert response.status_code==201
    adjustment=response.json['adjustment']
    assert (adjustment['outbound_g'],adjustment['stock_g'])==('81576/125','18424/125')
    assert (adjustment['difference'],adjustment['inventory'])==('978912/15625','221088/15625')
    merged=client.get('/api/closing/2026-11').json
    assert merged['totals']==sealed['totals']
    assert merged['remaining_pending_g']=='0'
    assert (merged['adjustments']['difference'],merged['adjustments']['inventory'])==('978912/15625','221088/15625')


def test_supplement_booked_once_concurrently(tmp_path,files):
    ledger=Ledger(tmp_path/'ledger')
    reports=compute(files)
    close_period(ledger,reports,'2026-11','run-a',{key:Fraction(80) for key in ('BR-08','CO-04')})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:add_supplement(ledger,'2026-11','ET-01',Fraction(96)),range(4)))
    assert sum(inserted for _,inserted in results)==1
    assert len({row['booked_at'] for row,_ in results})==1 and len(ledger.supplements('2026-11'))==1


def test_supplement_duplicate_returns_existing(client,tmp_path):
    run=archive(client)
    close(client,run)
    first=client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    again=client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    assert (first.status_code,again.status_code)==(201,200)
    assert again.json==first.json and len(list((tmp_path/'ledger').glob('supplement-*.json')))==1


def test_supplement_conflict_price(client):
    run=archive(client)
    close(client,run)
    client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    conflict=client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'88'})
    assert (conflict.status_code,conflict.json['code'])==(409,'SUPPLEMENT_SEALED')
    assert client.get('/api/closing/2026-11').json['supplements'][0]['unit_price']=='96'


def test_supplement_requires_closing(client):
    response=client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    assert (response.status_code,response.json['code'])==(404,'NOT_CLOSED')


def test_supplement_not_pending(client):
    run=archive(client)
    close(client,run,prices={**PRICES,'ET-01':'96'})
    response=client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    assert (response.status_code,response.json['code'])==(422,'NOT_PENDING')


def test_rerun_old_month_never_double_counts(client):
    run=archive(client)
    close(client,run)
    client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    client.post(f'/api/runs/{run}/rerun')
    again=close(client,run)
    merged=client.get('/api/closing/2026-11').json
    assert again.status_code==200 and merged['totals']['booked_cost']=='412/5'
    assert len(merged['supplements'])==1 and merged['adjustments']['difference']=='978912/15625'
    assert merged['remaining_pending_g']=='0'


def test_bad_period_and_price(client):
    run=archive(client)
    assert close(client,run,period='2026-13').json['code']=='BAD_PERIOD'
    assert close(client,run,prices={**PRICES,'ET-01':'0'}).json['code']=='BAD_PRICE'
    close(client,run)
    response=client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'abc'})
    assert response.json['code']=='BAD_PRICE'


def test_balance_page_lists_closing(client):
    run=archive(client)
    close(client,run)
    client.post('/api/closing/2026-11/supplements',json={'origin':'ET-01','price':'96'})
    page=client.get(f'/ledger/balance?run={run}&period=2026-11').get_data(as_text=True)
    assert '月结卷联 · 2026-11' in page and '82.40 元' in page
    assert '补价单' in page and '62.65' in page and '14.15' in page and '已封账 · 待补清零' in page


def test_close_requires_archived_run(client):
    response=client.post('/api/closing',json={'period':'2026-11','run':'0'*64,'prices':{}})
    assert (response.status_code,response.json['code'])==(404,'NO_RUN')


MIXED={
    'origins':'origin,label,available,unit\nOA,甲地,600,g\nOB,乙地,400,g\n',
    'jobs':'work,label,measured,unit\nW1,一批,1000,g\nW2,二批,500,g\nW3,三批,250,g\n',
    'inputs':('ticket,work,source_kind,source,quantity,unit\nI1,W1,origin,OA,600,g\nI2,W1,origin,OB,400,g\n'
              'I3,W2,return,RT-1,500,g\nI4,W3,return,RT-2,250,g\n'),
    'outputs':('ticket,work,kind,gross,tare,unit\nP1,W1,pack,500,0,g\nRT-1,W1,return,500,0,g\n'
               'P2,W2,pack,250,0,g\nRT-2,W2,return,250,0,g\nP3,W3,pack,200,0,g\nRT-3,W3,return,50,0,g\n'),
}


def test_multi_level_return_pends_only_unpriced_share(client):
    run=client.post('/api/runs',json={'files':MIXED}).json['id']
    closing=close(client,run,prices={'OA':'100'}).json
    assert closing['origins']['OA']=={'pack_g':'570','loss_g':'0','hold_g':'0','return_g':'30',
                                      'used_g':'600','unit_price':'100','cost':'60','pending_g':'0'}
    row=closing['origins']['OB']
    assert (row['pack_g'],row['return_g'],row['pending_g'],row['cost'])==('380','20','400',None)
    assert closing['totals']['booked_cost']=='60' and closing['totals']['pending_g']=='400'
    supplement=client.post('/api/closing/2026-11/supplements',json={'origin':'OB','price':'50'}).json
    assert (supplement['adjustment']['difference'],supplement['adjustment']['inventory'])==('19','1')
