"""Bounded SQLite history reads; API processes never open TinyFlux."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import falcon
from hsm.db import connect

class HistoryResource:
    _ranges={"15m":15,"1h":60,"6h":360,"24h":1440,"7d":10080}
    def __init__(self,path:Path): self._path=path
    def on_get(self,request:falcon.Request,response:falcon.Response,**params:str)->None:
        span=request.get_param("range") or "1h"
        if span not in self._ranges: raise falcon.HTTPBadRequest(description="Unsupported history range")
        since=(datetime.now(timezone.utc)-timedelta(minutes=self._ranges[span])).isoformat()
        c=connect(self._path)
        try:
            rows=c.execute("SELECT sampled_at,state,cpu_pct,ram_used_bytes,disk_used_bytes,net_rx_bytes,net_tx_bytes FROM metrics_history WHERE container_id=? AND sampled_at>=? ORDER BY sampled_at DESC LIMIT 300",(request.context.route_params["container_id"],since)).fetchall()
        finally:c.close()
        response.media={"range":span,"items":[dict(zip(("sampled_at","state","cpu_pct","ram_used_bytes","disk_used_bytes","net_rx_bytes","net_tx_bytes"),row)) for row in reversed(rows)]}
