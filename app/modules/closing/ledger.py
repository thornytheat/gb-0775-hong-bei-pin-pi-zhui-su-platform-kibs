import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.errors import RoastError
from app.modules.runs.fingerprint import encode

ORIGIN=re.compile(r'[\w-]{1,40}\Z')


class Ledger:
    def __init__(self,directory):
        self.path=Path(directory)
        self.path.mkdir(parents=True,exist_ok=True)

    def seal(self,name,record):
        final=self.path/f'{name}.json'
        part=None
        try:
            with NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.path,prefix='.pending-',delete=False) as stream:
                part=Path(stream.name)
                stream.write(encode(record))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(part,final)
                return True
            except FileExistsError:
                return False
        finally:
            if part is not None:
                part.unlink(missing_ok=True)

    def read(self,name):
        try:
            return json.loads((self.path/f'{name}.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError,UnicodeError) as exc:
            raise RoastError('DAMAGED_LEDGER','月结账册不可读',409) from exc

    def seal_closing(self,record):
        return self.seal(f"closing-{record['period']}",record)

    def closing(self,period):
        return self.read(f'closing-{period}')

    def seal_supplement(self,record):
        if not ORIGIN.fullmatch(record['origin']):
            raise RoastError('BAD_ORIGIN','产地批名不可入账')
        return self.seal(f"supplement-{record['period']}-{record['origin']}",record)

    def supplement(self,period,origin):
        if not ORIGIN.fullmatch(origin):
            raise RoastError('BAD_ORIGIN','产地批名不可入账')
        return self.read(f'supplement-{period}-{origin}')

    def supplements(self,period):
        rows=[]
        for file in sorted(self.path.glob(f'supplement-{period}-*.json')):
            record=self.read(file.stem)
            if record is not None:
                rows.append(record)
        return rows
