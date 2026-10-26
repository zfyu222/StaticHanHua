import importlib.util,unittest,argparse
from pathlib import Path
spec=importlib.util.spec_from_file_location('pack',Path(__file__).with_name('create_release_patch.py'));pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
class GuideTests(unittest.TestCase):
    def test_player_order_links_and_history(self):
        a=argparse.Namespace(title='《测试》子非鱼AI汉化补丁 v1.2',install='解压到游戏目录，运行安装汉化.cmd。',upgrade='解压新版并运行安装汉化.cmd。',uninstall='运行卸载汉化.cmd。',introduction='简体中文汉化。',game_version='v1',platform='Windows',coverage='正文和菜单。',verification='菜单和提示显示正常。',download_url='https://example.com/releases/',mirror_url='https://example.com/share',share_name='test',game_name='test',feedback_url='https://example.com/feedback',feedback_name='测试反馈',release_date='2026-10-27',changes='简化安装。')
        text=pack.make_guide(a,'1.2','- v1.0：首次发布。\n- v1.1：修复。\n')
        self.assertLess(text.index('下载地址:'),text.index('安装汉化'))
        self.assertLess(text.index('安装汉化'),text.index('补丁升级'));self.assertLess(text.index('补丁升级'),text.index('卸载汉化'));self.assertLess(text.index('卸载汉化'),text.index('适用版本'))
        self.assertIn(a.mirror_url+'\n请订阅github地址或者网盘地址，以便在补丁更新时获得通知\n有问题请反馈至【腾讯文档】测试反馈\n'+a.feedback_url,text)
        self.assertTrue(text.endswith('- v1.2（2026-10-27）：简化安装。\n'))
        self.assertFalse(any(term in text for term in ['备份','拷贝','未决','未验证','代理','SHA256','报错日志']))
    def test_missing_optional_mirror_not_invented(self):
        a=argparse.Namespace(title='t',install='i',upgrade=None,uninstall='u',introduction='x',game_version='v',platform='w',coverage='c',verification=None,download_url='https://example.com/releases/',mirror_url=None,feedback_url=None,release_date='d',changes='c')
        text=pack.make_guide(a,'1.0','');self.assertNotIn('网盘分享',text);self.assertNotIn('腾讯文档',text);self.assertIn('下载地址:https://example.com/releases/\n请订阅',text)
if __name__=='__main__':unittest.main()
