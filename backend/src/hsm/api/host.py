from pathlib import Path
import falcon
from hsm.lxd.capacity import CapacityService
from hsm.lxd.discovery import DiscoveryError
class HostCapacityResource:
 def __init__(self, service:CapacityService): self.service=service
 def on_get(self, request:falcon.Request,response:falcon.Response):
  try: response.media=self.service.get(request.context.user.id)
  except DiscoveryError as error: raise falcon.HTTPServiceUnavailable(description=error.message) from error
