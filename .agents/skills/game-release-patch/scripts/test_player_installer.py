"""Exercise the actual player PowerShell in disposable game directories."""
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path

HERE=Path(__file__).parent
def sha(b): return hashlib.sha256(b).hexdigest()
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='zfy-installer-test-');self.root=Path(self.temp.name)
        (self.root/'汉化工具').mkdir();(self.root/'汉化工具/patch.ps1').write_text((HERE/'player_installer.ps1').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
        (self.root/'FixtureGame.exe').write_bytes(b'fixture-exe')
        (self.root/'asset').write_bytes(b'original')
        self.config={'exe':'FixtureGame.exe','exe_sha256':sha(b'fixture-exe'),'steam_app_id':'112100','originals':[{'path':'asset','sha256':sha(b'original')}], 'legacy':[{'path':'asset','sha256':sha(b'legacy')},{'path':'new','sha256':sha(b'legacy-new')}]}
        (self.root/'汉化工具/config.json').write_text(json.dumps(self.config),encoding='utf-8-sig')
        self.payload()
    def tearDown(self): self.temp.cleanup()
    def payload(self,value=b'patched'):
        files=[]
        for name,data in [('asset',value),('new',b'new-file')]:
            target=self.root/'汉化文件'/name;target.parent.mkdir(exist_ok=True);target.write_bytes(data)
            files.append({'path':name,'archive_path':'汉化文件/'+name,'sha256':sha(data)})
        (self.root/'PATCH_MANIFEST.json').write_text(json.dumps({'files':files}),encoding='utf-8')
    def run_action(self,action,success=True):
        r=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'汉化工具/patch.ps1'),'-Action',action,'-NoLaunch'],capture_output=True)
        self.assertEqual(r.returncode,0 if success else 1,r.stdout.decode(errors='replace')+r.stderr.decode(errors='replace'));return r.stdout
    def test_install_upgrade_uninstall(self):
        self.run_action('install');self.run_action('install');self.assertEqual((self.root/'asset').read_bytes(),b'patched')
        self.payload(b'upgrade');self.run_action('install');self.assertEqual((self.root/'asset').read_bytes(),b'upgrade')
        self.run_action('uninstall');self.assertEqual((self.root/'asset').read_bytes(),b'original');self.assertFalse((self.root/'new').exists())
        self.run_action('uninstall');self.run_action('install');self.run_action('uninstall');self.assertEqual((self.root/'asset').read_bytes(),b'original')
    def test_legacy_upgrade(self):
        (self.root/'asset').write_bytes(b'legacy');(self.root/'new').write_bytes(b'legacy-new')
        self.run_action('install');out=self.run_action('uninstall');self.assertIn(b'STEAM_RESTORE=True',out);self.assertFalse((self.root/'new').exists())
    def test_legacy_uninstall(self):
        (self.root/'asset').write_bytes(b'legacy');(self.root/'new').write_bytes(b'legacy-new')
        self.assertIn(b'STEAM_RESTORE=True',self.run_action('uninstall'));self.assertFalse((self.root/'new').exists())
    def test_corrupt_payload_no_changes(self):
        (self.root/'汉化文件/new').write_bytes(b'bad');self.run_action('install',False);self.assertEqual((self.root/'asset').read_bytes(),b'original');self.assertFalse((self.root/'new').exists())
    def test_external_change_preserved(self):
        self.run_action('install');(self.root/'new').write_bytes(b'user-change');self.run_action('uninstall',False);self.assertEqual((self.root/'new').read_bytes(),b'user-change');self.assertEqual((self.root/'asset').read_bytes(),b'patched')
    def test_path_escape_rejected(self):
        (self.root/'PATCH_MANIFEST.json').write_text(json.dumps({'files':[{'path':'../outside','archive_path':'../outside','sha256':'x'}]}));self.run_action('install',False);self.assertEqual((self.root/'asset').read_bytes(),b'original')
if __name__=='__main__':unittest.main()
