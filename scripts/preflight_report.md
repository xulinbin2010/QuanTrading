# IBKR Live Trading Preflight Report

**Date:** 2026-04-23 09:04:22 UTC  
**Total runtime:** 0.9s  
**Tests passed:** 32  
**Tests failed:** 0  

## Summary

| Test Class | File | Passed | Failed | Time | Status |
|------------|------|-------:|-------:|-----:|--------|
| `connection_drops` | `tests/test_connection.py` | 5 | 0 | 0.76s | ✅ PASS |
| `partial_fills` | `tests/test_partial_fills.py` | 6 | 0 | 0.1s | ✅ PASS |
| `sector_limits` | `tests/test_sector_limits.py` | 7 | 0 | 0.06s | ✅ PASS |
| `position_sizing` | `tests/test_position_sizing.py` | 7 | 0 | 0.75s | ✅ PASS |
| `zombie_orders` | `tests/test_zombie_orders.py` | 7 | 0 | 0.1s | ✅ PASS |

---

## Verdict: ✅ GO

All tests passed. Safe to proceed with live trading deployment.
