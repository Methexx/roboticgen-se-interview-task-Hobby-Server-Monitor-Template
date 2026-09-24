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

class ConsumptionResource:
 def __init__(self,path:Path): self._path=path
 def on_get(self,request:falcon.Request,response:falcon.Response,**params:str)->None:
  span=request.get_param("range") or "1h"
  if span not in HistoryResource._ranges: raise falcon.HTTPBadRequest(description="Unsupported consumption range")
  since=(datetime.now(timezone.utc)-timedelta(minutes=HistoryResource._ranges[span])).isoformat();c=connect(self._path)
  try:
   row=c.execute("SELECT COUNT(*),AVG(cpu_pct),MAX(cpu_pct),AVG(ram_used_bytes),MAX(ram_used_bytes),MIN(net_rx_bytes),MAX(net_rx_bytes),MIN(net_tx_bytes),MAX(net_tx_bytes) FROM metrics_history WHERE container_id=? AND sampled_at>=?",(request.context.route_params['container_id'],since)).fetchone()
  finally:c.close()
  response.media={"range":span,"samples":row[0],"cpu_average":row[1],"cpu_peak":row[2],"ram_average_bytes":row[3],"ram_peak_bytes":row[4],"net_rx_bytes":None if row[5] is None or row[6] is None else row[6]-row[5],"net_tx_bytes":None if row[7] is None or row[8] is None else row[8]-row[7]}
