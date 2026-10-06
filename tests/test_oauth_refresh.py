"""Synthetic OAuth HTTP exchanges only; never production storage or network."""
import asyncio
import json
import time

import httpx
import pytest
from mcp.shared.auth import OAuthToken, OAuthClientInformationFull, OAuthClientMetadata
from trader.broker import TokenFile
from trader.oauth_refresh import UnattendedOAuth, credential_health, replayable
from trader.models import Unsafe

RESOURCE='https://broker.example/mcp'
ISSUER='https://auth.example'


@pytest.fixture
def wire(tmp_path):
    storage=TokenFile(tmp_path/'oauth.json')
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(client_id='synthetic-client',issuer=ISSUER,redirect_uris=['http://localhost/callback'])))
    asyncio.run(storage.set_tokens(OAuthToken(access_token='synthetic-old',refresh_token='synthetic-refresh',token_type='Bearer',expires_in=3600)))
    calls=[];mode={'refresh':200,'endpoint_401':False,'first_401':False,'issuer':ISSUER,'resource':RESOURCE,'body':None}
    def transport(request):
        calls.append((request.method,str(request.url)))
        if 'oauth-protected-resource' in request.url.path:
            return httpx.Response(200,json={'resource':mode['resource'],'authorization_servers':[ISSUER]})
        if 'oauth-authorization-server' in request.url.path:
            return httpx.Response(200,json={'issuer':mode['issuer'],'authorization_endpoint':ISSUER+'/authorize','token_endpoint':ISSUER+'/token','response_types_supported':['code']})
        if request.url.path=='/token':
            assert request.headers.get('Authorization') is None
            assert b'grant_type=refresh_token' in request.content
            if mode['body'] is not None:return httpx.Response(200,content=mode['body'])
            return httpx.Response(mode['refresh'],json={'access_token':'synthetic-new','token_type':'Bearer','expires_in':7200})
        count=sum(url==RESOURCE for _,url in calls)
        return httpx.Response(401 if mode['endpoint_401'] or mode['first_401'] and count==1 else 200,json={'ok':True})
    async def forbidden(*args):raise AssertionError('Interactive callback invoked')
    def auth():return UnattendedOAuth(server_url=RESOURCE,client_metadata=OAuthClientMetadata(redirect_uris=['http://localhost/callback']),storage=storage,redirect_handler=forbidden,callback_handler=forbidden)
    async def request(body=None,provider=None):
        async with httpx.AsyncClient(auth=provider or auth(),transport=httpx.MockTransport(transport)) as client:
            return await client.post(RESOURCE,json=body or {'jsonrpc':'2.0','id':1,'method':'initialize'})
    return storage,calls,mode,request,auth


def expire(storage):
    data=storage.read()['token_timing'];data.update(saved_at=time.time()-4000,expires_at=time.time()-1)
    storage.write_fields(token_timing=data)


def test_valid_token_no_refresh_and_two_fresh_instances(wire):
    storage,calls,mode,request,auth=wire
    assert asyncio.run(request()).status_code==200
    assert asyncio.run(request()).status_code==200
    assert calls==[('POST',RESOURCE)]*2


@pytest.mark.parametrize('mode_name',['expired','unknown_expiry','near_expiry','wrong_digest','future_saved'])
def test_expiry_restored_or_refreshed_before_initialize(wire,mode_name):
    storage,calls,mode,request,auth=wire
    if mode_name=='expired':expire(storage)
    else:
        data=storage.read()['token_timing']
        if mode_name=='unknown_expiry':data={}
        elif mode_name=='near_expiry':data['expires_at']=time.time()+10
        elif mode_name=='wrong_digest':data['access_digest']='unrelated'
        else:data['saved_at']=time.time()+10
        storage.write_fields(token_timing=data)
    assert asyncio.run(request()).status_code==200
    assert sum(url.endswith('/token') for _,url in calls)==1
    assert storage.read()['tokens']['refresh_token']=='synthetic-refresh'
    assert storage.path.stat().st_mode&0o077==0
    calls.clear();assert asyncio.run(request()).status_code==200
    assert calls==[('POST',RESOURCE)]


def test_401_refreshes_once_on_initialize(wire):
    storage,calls,mode,request,auth=wire;mode['first_401']=True
    assert asyncio.run(request()).status_code==200
    assert sum(url.endswith('/token') for _,url in calls)==1
    assert sum(url==RESOURCE for _,url in calls)==2


def test_second_401_is_durable_failure_not_infinite_refresh(wire):
    storage,calls,mode,request,auth=wire;mode['endpoint_401']=True
    with pytest.raises(Unsafe,match='no retry'):asyncio.run(request())
    assert storage.read()['refresh_failure']['code']=='401_AFTER_REFRESH'
    n=len(calls)
    with pytest.raises(Unsafe,match='blocked'):asyncio.run(request())
    assert len(calls)==n


@pytest.mark.parametrize('status',[400,401,429,500,302])
def test_refresh_rejection_persists_and_fresh_worker_cannot_spin(wire,status):
    storage,calls,mode,request,auth=wire;expire(storage);mode['refresh']=status
    with pytest.raises(Unsafe,match='rejected'):asyncio.run(request())
    assert sum(url==RESOURCE for _,url in calls)==0
    n=len(calls)
    with pytest.raises(Unsafe,match='blocked'):asyncio.run(request())
    assert len(calls)==n and storage.read()['tokens']['access_token']=='synthetic-old'


@pytest.mark.parametrize('body',[b'not-json',b'{}',b'{"access_token":"synthetic-new","token_type":"Bearer","expires_in":1}'])
def test_malformed_refresh_response_is_sanitized(wire,body,caplog):
    storage,calls,mode,request,auth=wire;expire(storage);mode['body']=body
    with pytest.raises(Unsafe,match='Invalid OAuth refresh'):asyncio.run(request())
    assert 'synthetic-new' not in caplog.text


@pytest.mark.parametrize('field,value',[('issuer','https://wrong.example'),('resource','https://wrong.example/mcp')])
def test_metadata_identity_mismatch_cannot_receive_refresh_credential(wire,field,value):
    storage,calls,mode,request,auth=wire;expire(storage);mode[field]=value
    with pytest.raises(Exception):asyncio.run(request())
    assert not any(url.endswith('/token') for _,url in calls)


@pytest.mark.parametrize('tool',['place_equity_order','cancel_equity_order','unknown_tool'])
def test_401_during_mutation_is_never_replayed_or_refreshed(wire,tool):
    storage,calls,mode,request,auth=wire;mode['endpoint_401']=True
    body={'method':'tools/call','params':{'name':tool}}
    with pytest.raises(Unsafe,match='no replay'):asyncio.run(request(body))
    assert calls==[('POST',RESOURCE)]


def test_read_refresh_requires_reconnect_for_account_pinning(wire):
    storage,calls,mode,request,auth=wire;mode['first_401']=True
    with pytest.raises(Unsafe,match='revalidate account'):
        asyncio.run(request({'method':'tools/call','params':{'name':'get_equity_orders'}}))
    assert sum(url==RESOURCE for _,url in calls)==1
    mode['first_401']=False
    assert asyncio.run(request()).status_code==200


def test_expired_token_cannot_dispatch_mutation_before_repin(wire):
    storage,calls,mode,request,auth=wire;expire(storage)
    with pytest.raises(Unsafe,match='revalidate account'):
        asyncio.run(request({'method':'tools/call','params':{'name':'place_equity_order'}}))
    assert not any(url==RESOURCE for _,url in calls)


def test_refresh_inflight_marker_survives_crash(wire):
    storage,calls,mode,request,auth=wire
    storage.write_fields(refresh_failure={'code':'REFRESH_IN_FLIGHT'})
    with pytest.raises(Unsafe,match='blocked'):asyncio.run(request())
    assert not calls


def test_readiness_never_exposes_tokens_and_unknown_lifetime_not_certified(wire):
    storage,*_=wire
    h=credential_health(storage,time.time()+100)
    assert h['covers_session'] and not h['refresh_proven']
    assert 'synthetic' not in json.dumps(h)
    storage.write_fields(token_timing={})
    assert not credential_health(storage,time.time()+100)['covers_session']


def test_fresh_auth_context_does_not_restart_relative_token_lifetime(wire):
    storage,calls,mode,request,auth=wire;expire(storage)
    assert asyncio.run(request()).status_code==200
    assert any(url.endswith('/token') for _,url in calls)


def test_symlink_credential_store_rejected(tmp_path):
    target=tmp_path/'target';target.write_text('{}');target.chmod(0o600)
    p=tmp_path/'oauth.json';p.symlink_to(target)
    with pytest.raises(OSError):TokenFile(p).read()


def test_two_concurrent_clients_rotate_only_once(wire):
    storage,calls,mode,request,auth=wire;expire(storage)
    async def run():return await asyncio.gather(request(),request())
    assert all(r.status_code==200 for r in asyncio.run(run()))
    assert sum(url.endswith('/token') for _,url in calls)==1


def test_credential_replacement_invalidates_old_refresh_evidence(wire):
    storage,calls,mode,request,auth=wire;expire(storage);asyncio.run(request())
    assert credential_health(storage,time.time()+100)['refresh_proven']
    asyncio.run(storage.set_tokens(OAuthToken(access_token='synthetic-relogin',refresh_token='synthetic-new-refresh',token_type='Bearer',expires_in=3600)))
    assert not credential_health(storage,time.time()+100)['refresh_proven']


def test_auth_redirect_is_not_a_blind_refresh_post_retry(wire):
    storage,calls,mode,request,auth=wire;expire(storage)
    async def run():
        sent=[]
        def handler(req):
            sent.append(str(req.url))
            if 'oauth-protected-resource' in req.url.path:return httpx.Response(307,headers={'Location':str(req.url)+'-redirect'})
            pytest.fail('Unexpected credential dispatch')
        async with httpx.AsyncClient(auth=auth(),transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(Unsafe):await client.post(RESOURCE,json={'method':'initialize'})
        assert len(sent)==1
    asyncio.run(run())
