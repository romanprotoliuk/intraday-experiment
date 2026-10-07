"""Real SDK + in-memory HTTP: never contacts Robinhood or submits orders."""
import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace
import httpx
import pytest
from mcp.shared.auth import OAuthToken, OAuthClientInformationFull
from mcp.shared.exceptions import McpError
from trader import broker as module
from trader.broker import READ_TOOLS, TokenFile, connect, discover_tools, failure_types
from trader.models import Unsafe


@pytest.fixture
def wire(tmp_path, monkeypatch):
    path = tmp_path/'oauth.json'
    asyncio.run(TokenFile(path).set_tokens(OAuthToken(access_token='test-only',token_type='Bearer',expires_in=3600)))
    state = SimpleNamespace(calls=[], clients=[], discovery_delay=0, drop_account=False, missing=False,refreshes=0)
    async def handle(request):
        if 'oauth-protected-resource' in request.url.path:
            return httpx.Response(200,json={'resource':module.ENDPOINT,'authorization_servers':['https://auth.example']})
        if 'oauth-authorization-server' in request.url.path:
            return httpx.Response(200,json={'issuer':'https://auth.example','authorization_endpoint':'https://auth.example/authorize','token_endpoint':'https://auth.example/token','response_types_supported':['code']})
        if request.url.host=='auth.example' and request.url.path=='/token':
            state.refreshes+=1
            return httpx.Response(200,json={'access_token':'synthetic-rotated','refresh_token':'synthetic-next-refresh','token_type':'Bearer','expires_in':3600})
        if request.method == 'GET':
            return httpx.Response(405)
        assert request.method == 'POST', 'No DELETE or other method expected'
        message = json.loads(request.content)
        method = message['method']
        state.calls.append(method)
        if method == 'notifications/initialized':
            return httpx.Response(202)
        if method == 'initialize':
            result = dict(protocolVersion='2025-11-25',capabilities={'tools':{}},serverInfo={'name':'mock','version':'1'})
        elif method == 'tools/list':
            await asyncio.sleep(state.discovery_delay)
            names = READ_TOOLS - {'get_portfolio'} if state.missing else READ_TOOLS
            result = dict(tools=[dict(name=name,inputSchema={'type':'object'},outputSchema=None) for name in sorted(names)])
        elif method == 'tools/call':
            assert message['params']['name'] == 'get_accounts', 'No writes permitted in connection tests'
            if state.drop_account:
                return httpx.Response(200,headers={'Content-Type':'text/event-stream'},content=': keepalive\n\n')
            result = dict(content=[{'type':'text','text':json.dumps({'data':{'accounts':[]}})}],isError=False)
        else:
            raise AssertionError(method)
        # Exercise SSE framing with the actual supported SDK transport.
        body = 'event: message\ndata: '+json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result})+'\n\n'
        return httpx.Response(200,headers={'Content-Type':'text/event-stream','Mcp-Session-Id':'test-session'},content=body)
    original = httpx.AsyncClient
    def client(**kwargs):
        obj = original(transport=httpx.MockTransport(handle),**kwargs)
        state.clients.append(obj)
        assert obj.timeout.read == 300
        return obj
    monkeypatch.setattr(module.httpx,'AsyncClient',client)
    return path,state


def test_discover_before_call_and_noninteractive_reconnect(wire):
    path,state = wire
    state.discovery_delay = .05
    async def run():
        for _ in range(2):
            async with connect(path) as b:
                b.timeout = .02  # discovery deliberately takes longer than this
                assert b.connection_info['initialized']
                assert set(b.catalog) == READ_TOOLS
                assert await b.call('get_accounts',{}) == {'accounts':[]}
            assert state.clients[-1].is_closed
    asyncio.run(run())
    assert state.calls == ['initialize','notifications/initialized','tools/list','tools/call']*2


def test_real_sdk_expired_credentials_refresh_then_two_clean_connections(wire):
    path,state=wire;storage=TokenFile(path)
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(client_id='synthetic-client',issuer='https://auth.example',redirect_uris=['http://localhost/callback'])))
    asyncio.run(storage.set_tokens(OAuthToken(access_token='synthetic-old',refresh_token='synthetic-refresh',token_type='Bearer',expires_in=3600)))
    storage.write_fields(token_timing={})
    async def run():
        for _ in range(2):
            async with connect(path) as broker:
                assert broker.live_guard is None and broker.cancel_guard is None
                assert await broker.call('get_accounts',{})=={'accounts':[]}
    asyncio.run(run())
    assert state.refreshes==1 and state.calls.count('initialize')==2
    assert all(c.is_closed for c in state.clients)


def test_timeout_does_not_retry_or_break_clean_teardown(wire, caplog):
    path,state = wire
    state.drop_account = True
    async def run():
        try:
            async with connect(path) as b:
                b.timeout = .02
                await b.call('get_accounts',{})
        except Exception as exc:
            assert 'McpError' in failure_types(exc)
            from trader.persistent.recovery import classify
            diagnostic=classify(exc,dict(tool='get_accounts'))
            assert diagnostic['failure_class']=='TRANSIENT_READ_TRANSPORT_FAILURE'
            assert diagnostic['transport_kind']=='MCP_LOCAL_READ_TIMEOUT'
        else:
            pytest.fail('Missing response must time out')
    asyncio.run(run())
    assert state.calls.count('tools/call') == 1
    assert all(c.is_closed for c in state.clients)
    assert 'ClosedResourceError' not in caplog.text


def test_missing_required_tool_fails_before_account_call(wire):
    path,state = wire
    state.missing = True
    async def run():
        with pytest.raises(Unsafe,match='list_tools failed'):
            async with connect(path):
                pytest.fail('Unusable connection must not be yielded')
    asyncio.run(run())
    assert 'tools/call' not in state.calls
    assert state.clients[0].is_closed


def test_missing_credentials_never_opens_http(tmp_path, monkeypatch):
    def no_network(**kwargs):
        pytest.fail('HTTP must not open without saved credentials')
    monkeypatch.setattr(module.httpx,'AsyncClient',no_network)
    async def run():
        with pytest.raises(Unsafe,match='No standalone'):
            async with connect(tmp_path/'missing.json'):
                pass
    asyncio.run(run())


def test_discovery_repeated_cursor_is_rejected():
    class Session:
        async def list_tools(self,**kwargs):
            return SimpleNamespace(tools=[],nextCursor='same')
    with pytest.raises(Unsafe,match='Repeated'):
        asyncio.run(discover_tools(Session()))


def test_discovery_multiple_pages():
    class Session:
        async def list_tools(self,params=None):
            names = {'get_accounts'} if params is None else READ_TOOLS-{'get_accounts'}
            return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in names],nextCursor='second' if params is None else None)
    assert set(asyncio.run(discover_tools(Session()))) == READ_TOOLS
