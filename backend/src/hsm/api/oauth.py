"""Browser-bound Google authorization-code initiation and logout."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import base64, hashlib, secrets
from urllib.parse import urlencode
import falcon
from hsm.db import connect

def _hash(value:str)->str:return hashlib.sha256(value.encode()).hexdigest()
class OAuthLoginResource:
 def __init__(self,settings):self.s=settings
 def on_get(self,request,response):
  state=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(24);verifier=secrets.token_urlsafe(48);binding=secrets.token_urlsafe(24);now=datetime.now(timezone.utc)
  c=connect(self.s.database_path)
  try:
   with c:c.execute("INSERT INTO oauth_states(state_hash,browser_binding_hash,nonce,pkce_verifier,created_at,expires_at) VALUES(?,?,?,?,?,?)",(_hash(state),_hash(binding),nonce,verifier,now.isoformat(),(now+timedelta(minutes=10)).isoformat()))
  finally:c.close()
  challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
  response.set_cookie('hsm_oauth_binding',binding,secure=self.s.cookie_secure,http_only=True,same_site='Lax',path='/auth/callback')
  response.status=falcon.HTTP_302;response.set_header('Location','https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':self.s.google_client_id,'redirect_uri':self.s.google_redirect_uri,'response_type':'code','scope':'openid email profile','state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256'}))
class LogoutResource:
 def __init__(self,sessions):self.sessions=sessions
 def on_post(self,request,response):
  values=request.get_cookie_values('hsm_session');self.sessions.revoke(values[0] if values else None);response.unset_cookie('hsm_session');response.status=falcon.HTTP_204
