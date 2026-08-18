"""官方模型目录 API。"""
from fastapi import APIRouter, Depends, Query
from app.api.admin import require_admin
from app.catalog.service import health, list_models, run
from app.api.webhooks import deliver_events

router=APIRouter(prefix="/v1/catalog",tags=["catalog"])

@router.get("/health")
def catalog_health():
    return health()

@router.get("")
def models(provider: str|None=None):
    rows=list_models(provider); return {"count":len(rows),"models":rows}

@router.post("/run")
def run_catalog(admin: dict = Depends(require_admin)):
    result=run(); events=[]
    for e in result.pop("events",[]):
        events.append({"event":e["event"],"occurred_at":e["occurred_at"],"data_date":e["occurred_at"][:10],"model":{"provider":e["provider"],"model":e["model"],"canonical_model":e["model"],"channel":"official","region":"intl","currency":"USD","service_tier":"standard","modality":"text","billing_unit":"token","source":"official-page","source_url":e["source_url"]}})
    result["webhook"] = deliver_events(events)
    result["events"] = len(events)
    return result
