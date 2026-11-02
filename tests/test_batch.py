"""Offline tests for batch scheduling, folders, manifests and GUI. No network or account sign-in."""
import sys, types, tempfile, asyncio, json, time, zipfile, queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
telethon=types.ModuleType('telethon'); telethon.TelegramClient=object
telethon_tl=types.ModuleType('telethon.tl'); telethon_types=types.ModuleType('telethon.tl.types'); telethon_types.MessageMediaDocument=object; telethon_types.MessageMediaPhoto=object
sys.modules.update({'telethon':telethon,'telethon.tl':telethon_tl,'telethon.tl.types':telethon_types})
auth=types.ModuleType('telegram_auth'); auth.CODE_RESEND='resend'; auth.connect_preserving_sessions=None;sys.modules['telegram_auth']=auth
# Use actual importer/downloader modules, replacing only Google auth and Telegram stubs.
import telegram_channel_downloader as mod

class MockClient:
    def __init__(self):
        self.connected=True; self.scanners=0; self.max_scanners=0
        self.active=0; self.max_active=0
        self.names={'@first':SimpleNamespace(title='First Channel',id=111),
                    '@second':SimpleNamespace(title='Second Channel',id=222),
                    '@invalid':None, '@same':SimpleNamespace(title='First Channel',id=333)}
        self.messages={111:[100,101],222:[200,201],333:[300]}
        self.downloaded=[]
    async def get_entity(self, ref):
        await asyncio.sleep(.002)
        if self.names.get(ref) is None: raise ValueError('Channel not accessible')
        return self.names[ref]
    async def iter_messages(self, entity, reverse=True):
        self.scanners+=1;self.max_scanners=max(self.scanners,self.max_scanners)
        try:
            for msgid in self.messages[entity.id]:
                await asyncio.sleep(.02)
                yield SimpleNamespace(id=msgid,media=object(),document=object(),photo=None,video=None,
                       video_note=None,voice=None,audio=None,sticker=None, file=SimpleNamespace(name=f'file{msgid}.txt',ext='.txt'),date=None,text='')
        finally:
            self.scanners-=1
    async def download_media(self,msg,file,progress_callback=None):
        self.active+=1; self.max_active=max(self.max_active,self.active)
        try:
            await asyncio.sleep(.04)
            Path(file).parent.mkdir(exist_ok=True,parents=True)
            Path(file).write_bytes((f'hello-{msg.id}').encode())
            if progress_callback: progress_callback(9,9)
            self.downloaded.append(msg.id)
            return file
        finally: self.active-=1
    async def disconnect(self): self.connected=False
    def is_connected(self):return self.connected

class MockGui:
    def __init__(self):self.events=queue.Queue()

async def fake_connect(**kw):return CLIENT

def run_batch(tmp,channels, import_txt_in_scan=False, concurrency=2, parallel=2):
    global CLIENT
    CLIENT=MockClient()
    cfg=dict(mod.DEFAULT_CONFIG, api_id='123',api_hash='test',channel=channels[0],channels=channels,
             concurrency=concurrency,parallel_channels=parallel,output_dir=str(tmp/'out'),
             import_txt=str(tmp/'links.txt'),import_txt_in_scan=import_txt_in_scan,
             auto_download_links=False,smart_history=True)
    with patch.object(mod,'STATE_FILE',tmp/'state.json'), patch.object(mod,'connect_preserving_sessions', fake_connect):
        archiver=mod.TelegramArchiver(MockGui())
        asyncio.run(archiver.run(cfg,'+100000000'))
    events=[]
    while not archiver.gui.events.empty():events.append(archiver.gui.events.get())
    return cfg, events,CLIENT

def test_parse():
    parsed=mod.parse_channels(' @first\n@SECOND\n@second\nhttps://t.me/abc/\nt.me/bbb\n# ignored\n')
    assert parsed==['@first','@SECOND','https://t.me/abc','https://t.me/bbb'],parsed
    assert mod.parse_channels(['@x', '@x'])==['@x']

def test_parallel_archives_and_failure():
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        cfg,ev,client=run_batch(tmp,['@first','@second','@invalid'],parallel=2)
        assert client.max_scanners>=2,client.max_scanners
        assert client.max_active<=2,client.max_active
        assert set(client.downloaded)=={100,101,200,201}
        assert (tmp/'out'/'First Channel'/'100_file100.txt').is_file()
        assert (tmp/'out'/'Second Channel'/'200_file200.txt').is_file()
        assert (tmp/'out'/'First Channel.zip').is_file()
        assert (tmp/'out'/'Second Channel.zip').is_file()
        assert (tmp/'out'/'CHANNEL_BATCH_REPORT.txt').is_file()
        assert 'failed' in (tmp/'out'/'CHANNEL_BATCH_REPORT.txt').read_text()
        assert len([e for e in ev if e[0]=='batch_done'])==1
        assert len([e for e in ev if e[0]=='channel_progress'])>=4
        with zipfile.ZipFile(tmp/'out'/'First Channel.zip') as z:
            assert z.testzip() is None
        state=json.loads((tmp/'state.json').read_text())
        assert set(state['downloaded'])=={'111','222'}
        assert client.connected is True # outer GUI wrapper closes session

def test_same_names_distinct_dirs():
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d);cfg,ev,client=run_batch(tmp,['@first','@same'])
        assert (tmp/'out'/'First Channel'/'100_file100.txt').is_file()
        assert (tmp/'out'/'First Channel_333'/'300_file300.txt').is_file()

def test_same_entity_alias_skips_duplicate():
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        # Emulate alias resolving to the same channel ID.
        original=MockClient.__init__
        def init(self):
            original(self)
            self.names['@alias']=SimpleNamespace(title='First Channel',id=111)
        with patch.object(MockClient, '__init__', init):
            cfg, ev, client=run_batch(tmp, ['@first','@alias'])
        assert set(client.downloaded)=={100,101}
        assert any(x[0]=='channel_update' and x[1]=='@alias' and x[2]=='Skipped' for x in ev)

def test_resume_history():
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        cfg,ev,client=run_batch(tmp,['@first','@second'])
        cfg,ev,client2=run_batch(tmp,['@first','@second'])
        assert client2.downloaded==[],f'redownloaded {client2.downloaded}'
        assert any('History skip' in x[-1] for x in ev if x[0]=='channel_progress')

def test_only_one_txt_import_for_multiple_channels():
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        (tmp/'links.txt').write_text('https://example.com/test.zip\nhttps://example.com/test.zip')
        cfg,ev,client=run_batch(tmp,['@first','@second'],import_txt_in_scan=True)
        assert (tmp/'out'/'Imported_Links'/'Links'/'ALL_LINKS.txt').is_file()
        assert (tmp/'out'/'First Channel'/'Links'/'ALL_LINKS.txt').read_text().strip()==''
        assert (tmp/'out'/'Second Channel'/'Links'/'ALL_LINKS.txt').read_text().strip()==''
        assert any(x[0]=='batch_done' for x in ev)

if __name__=='__main__':
    for name,fn in list(globals().items()):
        if name.startswith('test_'):
            fn(); print('[PASS]',name)

# v3.4 regression coverage: 100 entries, configurable active pool, hard limits.
def test_100_channel_capacity():
    names = [f'@batch_{i:03d}' for i in range(100)]
    refs, parallel = mod.validate_channel_batch(names + ['@batch_000'], 100)
    assert refs == names and parallel == 100
    try:
        mod.validate_channel_batch(names + ['@batch_100'], 5)
    except ValueError as exc:
        assert '100' in str(exc)
    else:
        raise AssertionError('101 channels not rejected')
    for bad in ('0','101','abc','5.5'):
        try:
            mod.validate_channel_batch(['@a'], bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Invalid worker count accepted: {bad!r}')


def test_one_hundred_jobs_keep_shared_session_and_worker_limit():
    class ManyClient:
        def __init__(self): self.connected=True
        async def get_entity(self, ref):
            return SimpleNamespace(title=f'Batch {ref}',id=int(ref.split('_')[-1])+1000)
    async def fake_many_connect(**kw): return ManyClient()
    async def exercise(parallel):
        busy=0;maximum=0
        async def fake_process(self,cfg,entity,out_root,source_file,source_ref,**kwargs):
            nonlocal busy,maximum
            busy+=1; maximum=max(maximum,busy)
            await asyncio.sleep(.002)
            busy-=1
            self.gui.events.put(('channel_update',source_ref,'Complete',1,1,'Mock download complete'))
            return {'channel':source_ref,'status':'complete','folder':str(out_root)}
        with tempfile.TemporaryDirectory() as d:
            tmp=Path(d)
            names=[f'@batch_{i:03d}' for i in range(100)]
            cfg=dict(mod.DEFAULT_CONFIG, api_id='123',api_hash='test',channel=names[0],channels=names,
                     concurrency=4,parallel_channels=parallel,output_dir=str(tmp/'out'),
                     import_txt=str(tmp/'missing.txt'),import_txt_in_scan=False)
            with patch.object(mod,'STATE_FILE',tmp/'state.json'), \
                 patch.object(mod,'connect_preserving_sessions',fake_many_connect), \
                 patch.object(mod.TelegramArchiver,'_process_channel',fake_process):
                a=mod.TelegramArchiver(MockGui())
                await a.run(cfg,'+100000000')
            events=[]
            while not a.gui.events.empty(): events.append(a.gui.events.get())
            report=(tmp/'out'/'CHANNEL_BATCH_REPORT.txt').read_text()
            assert len([x for x in events if x[0]=='channel_update' and x[2]=='Complete'])==100
            assert 'Channels requested: 100' in report
            assert len(report.splitlines())>=103
            return maximum
    assert asyncio.run(exercise(5)) == 5
    assert asyncio.run(exercise(100)) == 100


def test_excess_batch_does_not_connect():
    async def never_connect(**kw):
        raise AssertionError('Network should not run for invalid list')
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d);names=[f'@many{i}' for i in range(101)]
        cfg=dict(mod.DEFAULT_CONFIG, api_id='123',api_hash='test',channel=names[0],channels=names,
                 parallel_channels=5,output_dir=str(tmp/'out'))
        with patch.object(mod,'connect_preserving_sessions',never_connect):
            try:
                asyncio.run(mod.TelegramArchiver(MockGui()).run(cfg,'+100000000'))
            except ValueError as e:
                assert 'Maximum 100 channels' in str(e)
            else:
                raise AssertionError('101 channel batch accepted')
