from legacy_support import isolate
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from PIL import Image
import imageio_ffmpeg

import wechat_rag as rag
import wechat_media as media


def picture(color='red', fmt='PNG'):
    output=io.BytesIO();Image.new('RGB',(120,80),color).save(output,format=fmt);return output.getvalue()


class CodecTests(unittest.TestCase):
    def test_v2_decryption_keeps_plain_middle_and_decodes_xor_tail(self):
        data=picture(fmt='JPEG');key=b'1234567890abcdef';xor=71
        head=data[:128];middle=data[128:-30];tail=data[-30:]
        fixture=media.V2+struct.pack('<II',len(head),len(tail))+b'\x01'+AES.new(key,AES.MODE_ECB).encrypt(pad(head,16))+middle+bytes(v^xor for v in tail)
        self.assertEqual(media.decode_dat(fixture,key,xor),data)
        with self.assertRaises(RuntimeError):media.decode_dat(fixture,b'wrong-key-1234567',xor)
        with self.assertRaises(RuntimeError):media.decode_dat(fixture[:20],key,xor)

    def test_legacy_xor_and_plain_images(self):
        data=picture();self.assertEqual(media.decode_dat(bytes(v^88 for v in data)),data)
        self.assertEqual(media.decode_dat(data),data)

    def test_xml_parser_keeps_only_hashes_not_credentials_or_urls(self):
        digest='1'*32
        data=f'wxid_example:\n<msg><img md5="{digest}" aeskey="SECRET" cdnthumburl="https://example.com/private"/></msg>'.encode()
        result=media.parse_metadata(data,b'\x22\x20'+b'2'*32,3)
        self.assertEqual(result,{'hashes':['1'*32,'2'*32]})
        with self.assertRaises(RuntimeError):media.parse_metadata(b'<!DOCTYPE x><msg/>',b'',3)

    def test_gif_extracts_multiple_frames(self):
        output=io.BytesIO();a=Image.new('RGB',(20,20),'red');b=Image.new('RGB',(20,20),'blue')
        a.save(output,format='GIF',save_all=True,append_images=[b],duration=100,loop=0)
        self.assertEqual(len(media.jpeg_frames(output.getvalue(),animated=True)),2)

    def test_video_extracts_real_frames_without_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'clip.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','color=c=blue:s=160x120:d=1',
                            '-c:v','libx264','-pix_fmt','yuv420p',str(file)],check=True,capture_output=True,timeout=20)
            frames=media.video_frames(file)
            self.assertGreaterEqual(len(frames),2)
            self.assertTrue(all(data.startswith(b'\xff\xd8\xff') for data,_ in frames))


class MediaFlowTests(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.private=self.root/'private';self.private.mkdir()
        self.reader=SimpleNamespace(root=self.root/'account/db_storage')
        self.target={'chat_id':'room@chatroom','display_name':'测试群','db_root':str(self.reader.root)}
        self.store=rag.Store(self.private/'index.sqlite3')
        self.row={'database':'message_0.db','local_id':1,'server_id':10,'create_time':1700000000,'sender_id':'alice',
                  'local_type':47,'text':None,'media_fingerprint':'original'}
        with self.store.db:
            self.store.upsert([self.row]);self.store.set_meta('peer_id','room@chatroom');self.store.set_meta('sender_names',{'alice':'小王'})
        self.service=media.MediaService(self.reader,self.target,self.store,self.private)
        with self.store.db:self.store.set_meta('binding',self.service.context)
        self.digest='a'*32;self.raw=f'<msg><emoji md5="{self.digest}"/></msg>'.encode()
        self.source=patch.object(self.service,'original',return_value=(self.raw,b''));self.source.start()
        cache=self.root/'account/cache/2026-10/Emoticon'/self.digest
        cache.parent.mkdir(parents=True);cache.write_bytes(picture())

    def tearDown(self):
        self.source.stop();self.store.close();self.tmp.cleanup()

    def test_local_preview_uses_no_network_and_asset_is_scoped(self):
        with patch.object(media.urllib.request,'urlopen') as network:
            result=self.service.prepare('M1');network.assert_not_called()
        self.assertEqual(len(result['frames']),1)
        self.assertIsNone(result['description'])
        self.assertTrue(self.service.asset('M1',0).startswith(b'\xff\xd8'))
        with self.assertRaises(ValueError):self.service.asset('M1',4)
        with self.assertRaises(ValueError):self.service.prepare('../.env')
        with self.assertRaises(ValueError):self.service.prepare('M2')

    def test_recognition_is_cached_searchable_and_labelled_as_ai(self):
        with patch.object(media,'visual_description',return_value=('蓝色小猫挥手，文字为“你好”。','test-vision')) as vision:
            self.service.recognize('M1',{})
            self.service.recognize('M1',{})
            self.assertEqual(vision.call_count,1)
        refs=rag.evidence(self.store,self.store.retrieve('小猫'))
        self.assertEqual(len(refs),1);self.assertTrue(refs[0]['content_recognized'])
        self.assertIn('AI 视觉识别',refs[0]['text']);self.assertIn('表情包抽样',refs[0]['media']['scope'])
        self.assertNotIn('蓝色小猫',self.service.prepare('M1')['scope'])

    def test_changed_message_invalidates_description_and_search(self):
        with patch.object(media,'visual_description',return_value=('蓝色小猫','test-vision')):
            self.service.recognize('M1',{})
        with self.store.db:self.store.upsert([dict(self.row,media_fingerprint='changed')])
        self.assertIsNone(self.store.db.execute('SELECT * FROM media_results').fetchone())
        self.assertEqual(self.store.retrieve('小猫'),[])

    def test_import_fallback_is_local_and_keeps_provenance(self):
        with patch.object(media.urllib.request,'urlopen') as network:
            result=self.service.import_file('M1',base64.b64encode(picture('blue')).decode());network.assert_not_called()
        self.assertIn('用户导入',result['scope']);self.assertIn('未自动校验',result['scope'])
        self.assertEqual(self.service.prepare('M1')['scope'],result['scope'])
        with self.assertRaises(ValueError):self.service.import_file('M1',base64.b64encode(b'not an image').decode())

    def test_changed_source_does_not_reuse_old_description(self):
        with patch.object(media,'visual_description',return_value=('蓝色小猫','test-vision')):
            self.service.recognize('M1',{})
        self.source.stop();self.source=patch.object(self.service,'original',return_value=(self.raw+b'\n',b''));self.source.start()
        self.assertIsNone(self.service.prepare('M1')['description'])
        self.assertEqual(self.store.retrieve('小猫'),[])

    def test_no_local_file_gives_actionable_error(self):
        (self.root/'account/cache/2026-10/Emoticon'/self.digest).unlink()
        with self.assertRaisesRegex(RuntimeError,'本机没有找到媒体文件'):
            self.service.prepare('M1')

    def test_vision_request_uses_images_and_does_not_return_keys(self):
        result=json.dumps({'choices':[{'message':{'content':'红色方块'}}]}).encode()
        with patch.object(media.urllib.request,'urlopen',return_value=io.BytesIO(result)) as network:
            text,model=media.visual_description([(picture(fmt='JPEG'),'第一帧')],'测试图片',{'DEEPSEEK_API_KEY':'dummy-test-placeholder'})
        self.assertEqual(text,'红色方块')
        request=network.call_args.args[0];body=json.loads(request.data)
        self.assertEqual(body['messages'][0]['content'][-1]['type'],'image_url')
        self.assertNotIn('dummy-test-placeholder',request.data.decode())

    def test_ask_automatically_recognizes_image_and_reuses_it_next_time(self):
        response=json.dumps({'choices':[{'message':{'content':'这张图上有蓝色小猫。[M1]'}}]}).encode()
        with patch.object(media,'MediaService',return_value=self.service), \
             patch.object(media,'visual_description',return_value=('画面是蓝色小猫','test-vision')) as vision, \
             patch.object(rag,'env_config',return_value={'DEEPSEEK_API_KEY':'dummy-test-placeholder'}), \
             patch.object(rag.urllib.request,'urlopen',side_effect=lambda *a,**kw:io.BytesIO(response)) as answer:
            first=rag.ask(self.store,'解释 M1 的图片',reader=self.reader,target=self.target)
            sent=json.loads(answer.call_args.args[0].data)
            self.assertIn('蓝色小猫',sent['messages'][1]['content'])
            self.assertTrue(first['sources'][0]['content_recognized'])
            self.assertEqual(first['media_processing']['recognized'],['M1'])
            second=rag.ask(self.store,'M1是什么意思',reader=self.reader,target=self.target)
            self.assertEqual(second['media_processing']['cached'],['M1'])
            self.assertEqual(second['media_processing']['attempted'],[])
            self.assertEqual(vision.call_count,1)

    def test_ask_reports_failed_image_without_aborting_other_evidence(self):
        response=json.dumps({'choices':[{'message':{'content':'此媒体文件缺失，无法确认画面。[M1]'}}]}).encode()
        with patch.object(media,'MediaService',return_value=self.service), \
             patch.object(self.service,'recognize',side_effect=RuntimeError('本机没有找到媒体文件')), \
             patch.object(rag,'env_config',return_value={'DEEPSEEK_API_KEY':'dummy-test-placeholder'}), \
             patch.object(rag.urllib.request,'urlopen',return_value=io.BytesIO(response)) as answer:
            result=rag.ask(self.store,'M1 的内容',reader=self.reader,target=self.target)
            self.assertEqual(result['citation_check'],'passed')
            self.assertEqual(result['media_processing']['failed'],[{'id':'M1','reason':'本机没有找到媒体文件'}])
            self.assertFalse(result['sources'][0]['content_recognized'])
            sent=json.loads(answer.call_args.args[0].data)
            self.assertIn('本机没有找到媒体文件',sent['messages'][1]['content'])

    def test_auto_recognition_is_bounded_and_prioritizes_images(self):
        with self.store.db:
            self.store.upsert([dict(self.row,local_id=i,server_id=i*10,local_type=3,create_time=1700000000+i) for i in range(2,6)])
        rows=[dict(row) for row in self.store.db.execute('SELECT * FROM messages ORDER BY id')]
        def recognized(message_id,config):
            with self.store.db:
                self.store.db.execute('INSERT INTO media_results(message_id,assets,scope,labels,description,model) VALUES(?,?,?,?,?,?)',
                                      (int(message_id[1:]),'[]','测试图片','[]','识别描述','test-vision'))
            return {'description':'识别描述'}
        with patch.object(media,'MediaService',return_value=self.service), \
             patch.object(self.service,'recognize',side_effect=recognized) as vision:
            refs,report=rag.recognize_evidence(self.store,rows,{},reader=self.reader,target=self.target)
        self.assertEqual(vision.call_count,3)
        self.assertEqual(report['recognized'],['M5','M4','M3'])
        self.assertEqual(report['deferred'],['M2','M1'])
        self.assertEqual(sum(ref['content_recognized'] for ref in refs),3)

    def test_unknown_reference_does_not_recognize_other_images(self):
        with patch.object(rag,'env_config',return_value={'DEEPSEEK_API_KEY':'dummy-test-placeholder'}), \
             patch.object(media,'visual_description') as vision,patch.object(rag.urllib.request,'urlopen') as answer:
            result=rag.ask(self.store,'解释 M9999 的图片',reader=self.reader,target=self.target)
        self.assertFalse(result['api_called']);self.assertEqual(result['sources'],[])
        vision.assert_not_called();answer.assert_not_called()

    def test_photos_question_can_retrieve_unrecognized_image(self):
        with self.store.db:self.store.upsert([dict(self.row,local_type=3)])
        self.assertEqual([row['id'] for row in self.store.retrieve('最近的照片是什么')],[1])
        self.assertEqual([row['id'] for row in self.store.retrieve('解释 M1图片')],[1])
        self.assertEqual(self.store.retrieve('M9999图片'),[])


if __name__=='__main__':unittest.main()
