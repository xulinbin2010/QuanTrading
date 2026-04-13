"""自选股 watchlist API —— 持久化到 data/watchlist.json"""
import json
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix='/api/watchlist', tags=['watchlist'])

_WATCHLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'watchlist.json'
)


def _load() -> list[str]:
    if not os.path.exists(_WATCHLIST_PATH):
        return []
    with open(_WATCHLIST_PATH, encoding='utf-8') as f:
        return json.load(f)


def _save(symbols: list[str]):
    os.makedirs(os.path.dirname(_WATCHLIST_PATH), exist_ok=True)
    with open(_WATCHLIST_PATH, 'w', encoding='utf-8') as f:
        json.dump(symbols, f)


class AddRequest(BaseModel):
    symbol: str


@router.get('/')
def get_watchlist():
    return {'symbols': _load()}


@router.post('/')
def add_symbol(body: AddRequest):
    sym = body.symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail='symbol 不能为空')
    lst = _load()
    if sym not in lst:
        lst.append(sym)
        _save(lst)
    return {'symbols': lst}


@router.delete('/{symbol}')
def remove_symbol(symbol: str):
    sym = symbol.upper().strip()
    lst = _load()
    if sym in lst:
        lst.remove(sym)
        _save(lst)
    return {'symbols': lst}
