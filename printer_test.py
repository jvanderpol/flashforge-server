import unittest
import datetime
import printer as printer_lib

PRINTING_STATUS = status=printer_lib.StatusResponse(status='BUILDING_FROM_SD', move_mode='MOVING', led=True, current_file='file')
PAUSED_STATUS = status=printer_lib.StatusResponse(status='BUILDING_FROM_SD', move_mode='PAUSED', led=True, current_file='file')

def progress(current, total):
  return printer_lib.ProgressResponse(printed_bytes=printer_lib.Progress(current=current, total=total), layers=printer_lib.Progress(current=0, total=0))

class ProgressMonitorTest(unittest.TestCase):

  def setUp(self):
    printer = None
    self.monitor = printer_lib.ProgressMonitor(printer)

  def testEmpty(self):
    self.assertIsNone(self.monitor.estimate_remaining_time())

  def testOnlyOne(self):
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(0, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 00, 00)))
    self.assertIsNone(self.monitor.estimate_remaining_time())

  def testTwoAtSamePercent(self):
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(0, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 0, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(0, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 1, 0)))
    self.assertIsNone(self.monitor.estimate_remaining_time())

  def testTwoAtDifferentPercents(self):
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(0, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 0, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(1, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 1, 0)))
    self.assertEqual(
        self.monitor.estimate_remaining_time(),
        printer_lib.EstimatedPrintingTimes(completed_seconds=60, remaining_seconds=60*99))

  def testThreeWithDuplicatePercents(self):
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(0, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 0, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(1, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 1, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(1, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 2, 0)))
    self.assertEqual(
        self.monitor.estimate_remaining_time(),
        printer_lib.EstimatedPrintingTimes(completed_seconds=120, remaining_seconds=60*98))

  def testWithPause(self):
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(0, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 0, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(1, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 1, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(1, 100),
          status=PAUSED_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 2, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(2, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 3, 0)))
    self.assertEqual(
        self.monitor.estimate_remaining_time(),
        printer_lib.EstimatedPrintingTimes(completed_seconds=120, remaining_seconds=60*98))

  def testWithMidStart(self):
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(50, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 0, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(51, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 1, 0)))
    self.assertEqual(
        self.monitor.estimate_remaining_time(),
        printer_lib.EstimatedPrintingTimes(completed_seconds=60*51, remaining_seconds=60*49))

  def testWithPausedEnd(self):
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(0, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 1, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(1, 100),
          status=PRINTING_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 2, 0)))
    self.monitor._logged_progress.append(
        printer_lib.LoggedProgress(
          progress=progress(1, 100),
          status=PAUSED_STATUS,
          timestamp=datetime.datetime(2026, 1, 31, 20, 3, 0)))
    self.assertEqual(
        self.monitor.estimate_remaining_time(),
        printer_lib.EstimatedPrintingTimes(completed_seconds=60, remaining_seconds=60*99))


if __name__ == '__main__':
    unittest.main()
