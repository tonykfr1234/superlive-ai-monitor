import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from daily_report import DailyReports, TAIPEI
from record_protocol import atomic_json, read_json


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stamp = self.at('2026-10-12T22:00:00')
        self.report = DailyReports(self.root, self.root/'videos', Mock(), clock=lambda: self.stamp)
        self.report.send = Mock(return_value=True)

    @staticmethod
    def at(value):
        return datetime.fromisoformat(value).replace(tzinfo=TAIPEI).timestamp()

    def trigger(self, event_id, when, **extra):
        self.report.record_trigger(dict(request_id=event_id, created_at=self.at(when), count=2, confidence=.9, **extra), event_id+'.mp4')

    def tick(self, when):
        self.stamp = self.at(when)
        self.report.tick()

    def csv_rows(self, phase='2300', day='2026-10-12'):
        path = self.root/'videos'/day/('SuperLive_Triggers_'+day+'_'+phase+'.csv')
        self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
        with path.open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.reader(stream))

    def test_2300_boundary_sort_deduplicate_and_excel_csv(self):
        self.trigger('later', '2026-10-12T22:58:00', trigger_type='MOVEMENT_ROLLOVER')
        self.trigger('early', '2026-10-12T09:00:00')
        self.trigger('early', '2026-10-12T09:00:00')
        self.tick('2026-10-12T22:59:59')
        self.report.send.assert_not_called()
        self.tick('2026-10-12T23:00:00')
        rows = self.csv_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual([row[4] for row in rows[1:]], ['early','later'])
        self.assertEqual(rows[2][1], 'MOVEMENT_ROLLOVER')
        self.assertEqual(rows[1][5], 'early.mp4')
        self.assertIn('+08:00', rows[1][0])
        self.tick('2026-10-12T23:01:00')
        self.report.send.assert_called_once()

    def test_zero_trigger_day_sends_header_only(self):
        self.tick('2026-10-12T23:00:00')
        self.assertEqual(len(self.csv_rows()), 1)
        self.assertEqual(self.report.send.call_args.args[0]['count'], 0)

    def test_send_at_2300_even_during_quiet_period(self):
        atomic_json(self.root/'runtime/telegram_quiet.json', dict(request_id='active', quiet_until=self.at('2026-10-12T23:01:00')))
        self.tick('2026-10-12T23:00:00')
        self.assertEqual(len(self.csv_rows()), 1)
        self.report.send.assert_called_once()
        self.tick('2026-10-12T23:01:00')
        self.report.send.assert_called_once()

    def test_failure_retry_survives_restart_without_repeating_success(self):
        self.report.send.side_effect = RuntimeError('network')
        self.tick('2026-10-12T23:00:00')
        job = read_json(self.report.jobs/'2026-10-12_2300.json')
        self.assertFalse(job['sent'])
        self.assertEqual(job['attempts'], 1)
        self.report = DailyReports(self.root, self.root/'videos', Mock(), clock=lambda: self.stamp)
        self.report.send = Mock(return_value=True)
        self.tick('2026-10-12T23:00:10')
        self.report.send.assert_not_called()
        self.tick('2026-10-12T23:00:21')
        self.report.send.assert_called_once()
        self.tick('2026-10-12T23:10:00')
        self.report.send.assert_called_once()

    def test_after_2300_is_excluded_and_no_midnight_supplement(self):
        self.trigger('before','2026-10-12T22:59:59')
        self.trigger('after','2026-10-12T23:00:00')
        self.tick('2026-10-12T23:00:00')
        self.tick('2026-10-13T00:00:15')
        self.assertEqual(len(self.csv_rows()),2)
        self.report.send.assert_called_once()
        self.assertEqual(list(self.report.jobs.glob('*_full.json')),[])

    def test_missed_days_send_oldest_first(self):
        self.tick('2026-10-14T23:00:00')
        self.assertEqual([call.args[0]['day'] for call in self.report.send.call_args_list],
                         ['2026-10-12', '2026-10-13', '2026-10-14'])

    def test_old_report_retry_blocks_newer_days_and_survives_restart(self):
        self.report.send.side_effect = RuntimeError('network')
        self.tick('2026-10-14T23:00:00')
        self.report.send.assert_called_once()
        self.assertEqual(self.report.send.call_args.args[0]['day'], '2026-10-12')
        self.report = DailyReports(self.root, self.root/'videos', Mock(), clock=lambda: self.stamp)
        self.report.send = Mock(return_value=True)
        self.tick('2026-10-14T23:00:10')
        self.report.send.assert_not_called()
        self.tick('2026-10-14T23:00:21')
        self.assertEqual([call.args[0]['day'] for call in self.report.send.call_args_list],
                         ['2026-10-12', '2026-10-13', '2026-10-14'])

    def test_missed_schedule_keeps_the_2300_cutoff(self):
        self.trigger('before','2026-10-12T22:59:59')
        self.trigger('after','2026-10-12T23:59:59')
        self.tick('2026-10-13T10:00:00')
        self.assertEqual(len(self.csv_rows()),2)
        self.assertEqual(self.report.send.call_args.args[0]['phase'],'2300')

    def test_old_full_jobs_are_ignored_and_unsent_snapshot_restored(self):
        self.report.generate(datetime(2026,10,12).date(),'2300')
        path=self.report.jobs/'2026-10-12_2300.json'
        job=read_json(path); job['superseded']=True; atomic_json(path,job)
        full=dict(job,phase='full',superseded=False)
        atomic_json(self.report.jobs/'2026-10-12_full.json',full)
        self.tick('2026-10-13T00:00:15')
        self.report.send.assert_called_once()
        self.assertEqual(self.report.send.call_args.args[0]['phase'],'2300')
        self.assertFalse(read_json(path).get('superseded',False))

    def test_legacy_evidence_import_and_journal_precedence(self):
        folder = self.root/'events'
        folder.mkdir()
        (folder/'person_events.csv').write_text('timestamp,event_type,count,confidence,request_id\n2026-10-12 08:00:00,PERSON_DETECTED,1,0.8,old\n2026-10-12 09:00:00,PERSON_DETECTED,1,0.8,new\n',encoding='utf-8-sig')
        self.trigger('new','2026-10-12T09:00:00')
        self.tick('2026-10-12T23:00:00')
        rows=self.csv_rows()
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[2][2],'2')

    def test_real_send_method_ignores_quiet_but_honors_shutdown(self):
        self.tick('2026-10-12T23:00:00')
        job=read_json(self.report.jobs/'2026-10-12_2300.json')
        atomic_json(self.root/'runtime/telegram_quiet.json',dict(request_id='active',quiet_until=self.stamp+120))
        with patch('daily_report.load_credentials',return_value=('test-token','test-chat')), \
                patch('requests.post',return_value=Mock(status_code=200,json=lambda:{'ok':True})) as post:
            self.assertTrue(DailyReports.send(self.report,job))
            self.assertTrue(post.call_args.args[0].endswith('/sendDocument'))
            self.assertIn('document',post.call_args.kwargs['files'])
        self.report.stop_event.set()
        with patch('daily_report.load_credentials',return_value=('test-token','test-chat')), \
                patch('requests.post') as post:
            self.assertFalse(DailyReports.send(self.report,job))
            post.assert_not_called()
