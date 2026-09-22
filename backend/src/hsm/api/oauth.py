"""Browser-bound Google authorization-code initiation and logout."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import base64, hashlib, secrets
from urllib.parse import urlencode
import falcon
from hsm.db import connect
from hsm.auth.sessions import SessionService
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import requests

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
class OAuthCallbackResource:
 def __init__(self,settings,sessions:SessionService):self.s,self.sessions=settings,sessions
 def on_get(self,request,response):
  state=request.get_param('state');code=request.get_param('code');bindings=request.get_cookie_values('hsm_oauth_binding')
  if not state or not code or not bindings: raise falcon.HTTPForbidden(description='OAuth transaction is invalid')
  c=connect(self.s.database_path)
  try:
   row=c.execute("SELECT browser_binding_hash,nonce,pkce_verifier,expires_at FROM oauth_states WHERE state_hash=?",(_hash(state),)).fetchone()
   if row is None or row[0]!=_hash(bindings[0]) or datetime.fromisoformat(row[3])<=datetime.now(timezone.utc): raise falcon.HTTPForbidden(description='OAuth transaction is invalid')
   c.execute("DELETE FROM oauth_states WHERE state_hash=?",(_hash(state),));c.commit()
  finally:c.close()
  token=requests.post('https://oauth2.googleapis.com/token',data={'code':code,'client_id':self.s.google_client_id,'client_secret':self.s.google_client_secret,'redirect_uri':self.s.google_redirect_uri,'grant_type':'authorization_code','code_verifier':row[2]},timeout=10).json().get('id_token')
  try: claims=id_token.verify_oauth2_token(token,google_requests.Request(),self.s.google_client_id)
  except Exception as error: raise falcon.HTTPForbidden(description='Google token is invalid') from error
  email=claims.get('email','').lower();sub=claims.get('sub');
  if claims.get('iss') not in {'accounts.google.com','https://accounts.google.com'} or claims.get('nonce')!=row[1] or not claims.get('email_verified') or not isinstance(sub,str): raise falcon.HTTPForbidden(description='Google claims are invalid')
  c=connect(self.s.database_path)
  try:
   user=c.execute('SELECT id,role,status,google_sub FROM users WHERE email=?',(email,)).fetchone()
   if user is None and email==self.s.bootstrap_admin_email and c.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0:
    c.execute("INSERT INTO users(email,google_sub,role,status,quota_ram_bytes,quota_cpu_cores,quota_disk_bytes,created_at) VALUES(?,?, 'admin','active',0,0,0,datetime('now'))",(email,sub));c.commit();user=c.execute('SELECT id,role,status,google_sub FROM users WHERE email=?',(email,)).fetchone()
   if user is None or user[2]=='revoked' or (user[3] is not None and user[3]!=sub): raise falcon.HTTPForbidden(description='Account is not invited')
   c.execute("UPDATE users SET google_sub=?,status='active',last_login_at=datetime('now') WHERE id=?",(sub,user[0]));c.commit()
  finally:c.close()
  session=self.sessions.create(user[0]);response.set_cookie('hsm_session',session,secure=self.s.cookie_secure,http_only=True,same_site='Lax',path='/');response.unset_cookie('hsm_oauth_binding');response.status=falcon.HTTP_302;response.set_header('Location','/containers' if user[1]=='admin' else '/dashboard')
