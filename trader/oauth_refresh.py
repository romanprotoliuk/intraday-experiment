"""Bounded noninteractive OAuth adapter for the pinned MCP SDK.

Uses SDK resource/issuer validation and refresh request construction. Never opens
a browser, registers a client, retries a broker mutation, or logs token values.
After refresh an established MCP session must reconnect and pin its account.
"""
import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import json
import math
import os
import time
from urllib.parse import urlparse

from mcp.client.auth import OAuthClientProvider
from mcp.client.auth import utils
from mcp.shared.auth import OAuthToken
from .models import Unsafe

VERSION = 'bounded-oauth-refresh-1'
REFRESH_MARGIN_SECONDS = 60


def token_digest(token):
    return hashlib.sha256(token.access_token.encode()).hexdigest()


def timing(storage, token):
    data=storage.read();record=data.get('token_timing',{})
    try:
        deadline=float(record['expires_at']);saved=float(record['saved_at'])
        if record['access_digest']!=token_digest(token) or not all(map(math.isfinite,(deadline,saved))):return None
        if not 0 < saved < deadline or saved>time.time():return None
        return deadline
    except (KeyError,TypeError,ValueError):return None


def credential_health(storage, horizon):
    """No refresh/network or credential values. Evidence, not a refresh promise."""
    try:
        data=storage.read();token=OAuthToken.model_validate(data['tokens']);deadline=timing(storage,token)
        failure=data.get('refresh_failure')
        return dict(present=True,expires_at_epoch=deadline,expiry_known=deadline is not None,
                    refresh_present=bool(token.refresh_token),client_present=bool(data.get('client')),
                    refresh_blocked=bool(failure),adapter_version=VERSION,
                    covers_session=bool(deadline and deadline>horizon+REFRESH_MARGIN_SECONDS and not failure),
                    refresh_proven=(data.get('refresh_verified') or {}).get('version')==VERSION and
                        (data.get('refresh_verified') or {}).get('access_digest')==token_digest(token))
    except (OSError,ValueError,TypeError,KeyError,Unsafe):
        return dict(present=False,expiry_known=False,covers_session=False,refresh_proven=False,refresh_blocked=True)


@asynccontextmanager
async def refresh_lock(storage, timeout=8):
    """Serialize rotating credentials across worker/doctor processes; bounded wait."""
    path=storage.path.with_suffix('.refresh.lock')
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        if os.fstat(fd).st_mode&0o077:raise Unsafe('OAuth refresh lock permissions invalid')
        deadline=time.monotonic()+timeout
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise Unsafe('OAuth refresh already in progress')
                await asyncio.sleep(.05)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def replayable(request):
    if request.method=='GET':return True
    try:
        body=json.loads(request.content)
        if not isinstance(body,dict):return False
        method=body.get('method')
        if method in ('initialize','ping','tools/list','notifications/initialized'):return True
        if method=='tools/call':
            from .broker import READ_TOOLS
            return body.get('params',{}).get('name') in READ_TOOLS
    except (ValueError,TypeError):pass
    return False


def initializes(request):
    try:return json.loads(request.content).get('method')=='initialize'
    except (ValueError,TypeError,AttributeError):return False


class UnattendedOAuth(OAuthClientProvider):
    async def async_auth_flow(self, request):
        # Do not inherit transparent auth-endpoint redirects: a refresh token is
        # a rotating credential and its POST must not be blindly replayed.
        flow=self._auth_flow(request)
        try:
            outgoing=await anext(flow)
            while True:
                response=yield outgoing
                outgoing=await flow.asend(response)
        except StopAsyncIteration:return
        finally:await flow.aclose()

    async def discover(self):
        """SDK discovery without full interactive authorization fallback."""
        for url in utils.build_protected_resource_metadata_discovery_urls(None,self.context.server_url):
            response=yield utils.create_oauth_metadata_request(url)
            if response.status_code>=500 or response.status_code in (429,301,302,307,308):
                raise Unsafe('OAuth resource discovery unavailable')
            prm=await utils.handle_protected_resource_response(response)
            if prm:
                await self._validate_resource_match(prm)
                self.context.protected_resource_metadata=prm
                self.context.auth_server_url=self._select_authorization_server([str(x) for x in prm.authorization_servers])
                break
        else:raise Unsafe('Validated OAuth resource metadata required')
        issuer=self._expected_issuer()
        if urlparse(issuer).scheme!='https' or not utils.credentials_match_issuer(self.context.client_info,issuer,self.context.client_metadata_url):
            raise Unsafe('OAuth credential issuer mismatch')
        for url in utils.build_oauth_authorization_server_metadata_discovery_urls(self.context.auth_server_url,self.context.server_url):
            response=yield utils.create_oauth_metadata_request(url)
            ok,metadata=await utils.handle_auth_metadata_response(response)
            if not ok:raise Unsafe('OAuth issuer discovery failed')
            if metadata:
                utils.validate_metadata_issuer(metadata,issuer)
                if not metadata.token_endpoint or urlparse(str(metadata.token_endpoint)).scheme!='https':
                    raise Unsafe('Secure discovered token endpoint required')
                self.context.oauth_metadata=metadata
                break
        else:raise Unsafe('Validated OAuth issuer metadata required')

    async def refresh(self, rejected_digest=None):
        storage=self.context.storage
        async with refresh_lock(storage):
            await self._initialize()
            current=self.context.current_tokens
            if not current or not self.context.can_refresh_token():raise Unsafe('Interactive authentication required: refresh credential unavailable')
            failure=storage.read().get('refresh_failure')
            if failure:raise Unsafe('OAuth refresh blocked pending credential repair')
            deadline=timing(storage,current)
            # Another process may already have rotated the rejected credential.
            if deadline and deadline>time.time()+REFRESH_MARGIN_SECONDS and (rejected_digest is None or rejected_digest!=token_digest(current)):
                self.context.token_expiry_time=deadline
                return
            discovery=self.discover()
            try:
                request=await anext(discovery)
                while True:
                    response=yield request
                    request=await discovery.asend(response)
            except StopAsyncIteration:pass
            finally:await discovery.aclose()
            request=await self._refresh_token()
            # Persist BEFORE dispatch: timeout/crash may have rotated the token.
            storage.write_fields(refresh_failure={'code':'REFRESH_IN_FLIGHT','at':time.time()})
            response=yield request
            if response.status_code!=200:
                storage.write_fields(refresh_failure={'code':'REFRESH_REJECTED','http_status':response.status_code,'at':time.time()})
                raise Unsafe('OAuth refresh rejected; interactive credential repair required')
            try:
                updated=OAuthToken.model_validate_json(await response.aread())
                if not updated.expires_in or updated.expires_in<=REFRESH_MARGIN_SECONDS:
                    raise ValueError('Unusable token lifetime')
                if not updated.refresh_token:updated=updated.model_copy(update={'refresh_token':current.refresh_token})
            except Exception:
                storage.write_fields(refresh_failure={'code':'INVALID_REFRESH_RESPONSE','at':time.time()})
                raise Unsafe('Invalid OAuth refresh response; credential repair required') from None
            await storage.set_tokens(updated)
            storage.write_fields(refresh_failure=None,refresh_verified={'version':VERSION,'at':time.time(),'access_digest':token_digest(updated)})
            self.context.current_tokens=updated
            self.context.token_expiry_time=timing(storage,updated)

    async def _auth_flow(self, request):
        async with self.context.lock:
            await request.aread()
            await self._initialize()
            self.context.protocol_version=request.headers.get('mcp-protocol-version')
            token=self.context.current_tokens
            if not token:raise Unsafe('Production OAuth credentials unavailable')
            if self.context.storage.read().get('refresh_failure'):raise Unsafe('OAuth refresh blocked pending credential repair')
            deadline=timing(self.context.storage,token)
            refreshed=False
            if deadline is None or deadline<=time.time()+REFRESH_MARGIN_SECONDS:
                flow=self.refresh()
                try:
                    outgoing=await anext(flow)
                    while True:
                        response=yield outgoing
                        outgoing=await flow.asend(response)
                except StopAsyncIteration:pass
                finally:await flow.aclose()
                refreshed=True
                if not initializes(request):raise Unsafe('OAuth refreshed; reconnect and revalidate account before continuing')
            self._add_auth_header(request)
            used=token_digest(self.context.current_tokens)
            response=yield request
            if response.status_code!=401:return
            if not replayable(request):raise Unsafe('OAuth rejected mutation; broker reconciliation required, no replay')
            if refreshed:
                self.context.storage.write_fields(refresh_failure={'code':'401_AFTER_REFRESH','at':time.time()})
                raise Unsafe('OAuth rejected refreshed credential; no retry')
            flow=self.refresh(used)
            try:
                outgoing=await anext(flow)
                while True:
                    response=yield outgoing
                    outgoing=await flow.asend(response)
            except StopAsyncIteration:pass
            finally:await flow.aclose()
            if not initializes(request):raise Unsafe('OAuth refreshed; reconnect and revalidate account before continuing')
            self._add_auth_header(request)
            response=yield request
            if response.status_code==401:
                self.context.storage.write_fields(refresh_failure={'code':'401_AFTER_REFRESH','at':time.time()})
                raise Unsafe('OAuth rejected refreshed credential; no retry')
