from pathlib import Path
import tempfile
import unittest
from labcore.io import read_json
from tools.opencode_cli import configure
from tools.package_source import source_files


class ProviderConfigurationTests(unittest.TestCase):
    def test_local_provider_has_key_reference_and_matching_rsi_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);result=configure(root)
            cfg=read_json(root/'.opencode/opencode.jsonc')
            self.assertFalse(result['credentials_written'])
            self.assertEqual(cfg['providers']['lab-deepseek']['env'],['DEEPSEEK_API_KEY'])
            self.assertEqual(read_json(root/'rsi/config.local.json')['model'],cfg['model'])
            self.assertNotIn(root/'.opencode/opencode.jsonc',list(source_files(root)))
            with self.assertRaises(FileExistsError):configure(root)

    def test_provider_endpoint_cannot_embed_a_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            for url in ['https://user:password@example.test/v1','https://example.test/v1?api_key=secret']:
                with self.assertRaises(ValueError):configure(Path(folder),base_url=url)


if __name__=='__main__':unittest.main()
