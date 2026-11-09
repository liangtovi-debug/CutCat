"""Explicit pytest plugin for exact, crash-readable bounded-run receipts.

Loaded only by run_bounded_tests.py. It does not select, retry, or skip tests.
The append-only journal is flushed after each event; the final receipt is atomic.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def pytest_addoption(parser):
    group = parser.getgroup('bounded execution evidence')
    group.addoption('--bounded-receipt', required=True)
    group.addoption('--bounded-events', required=True)


def pytest_configure(config):
    config.pluginmanager.register(Receipt(config), 'bounded-execution-receipt')


class Receipt:
    def __init__(self, config):
        self.path = Path(config.getoption('--bounded-receipt'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = Path(config.getoption('--bounded-events')).open('x', encoding='utf-8')
        self.began = time.monotonic()
        self.collected = []
        self.started = []
        self.finished = []
        self.reports = []
        self.collection_reports = []
        self.event('session_start')

    def event(self, kind, **data):
        self.stream.write(json.dumps({'event': kind, 'elapsed_seconds': time.monotonic() - self.began,
                                      'wall_time': time.time(), 'process_cpu_seconds': time.process_time(), **data}, ensure_ascii=True) + '\n')
        self.stream.flush()

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            result = {'nodeid': report.nodeid, 'outcome': report.outcome,
                      'detail': str(report.longrepr)}
            self.collection_reports.append(result)
            self.event('collection_report', **result)

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]
        self.event('collected', nodeids=self.collected)

    def pytest_runtest_logstart(self, nodeid, location):
        self.started.append(nodeid)
        self.event('started', nodeid=nodeid)

    def pytest_runtest_logreport(self, report):
        result = {'nodeid': report.nodeid, 'when': report.when, 'outcome': report.outcome,
                  'duration_seconds': report.duration, 'started_at': report.start, 'finished_at': report.stop}
        if hasattr(report, 'wasxfail'):
            result['wasxfail'] = str(report.wasxfail)
        if report.failed or report.skipped:
            result['detail'] = str(report.longrepr)
        self.reports.append(result)
        self.event('report', **result)

    def pytest_runtest_logfinish(self, nodeid, location):
        self.finished.append(nodeid)
        self.event('finished', nodeid=nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        result = {'schema': 1, 'exitstatus': int(exitstatus), 'collected': self.collected,
                  'started': self.started, 'finished': self.finished,
                  'reports': self.reports, 'collection_reports': self.collection_reports}
        self.event('session_finish', exitstatus=int(exitstatus))
        temporary = self.path.with_suffix('.pending')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def pytest_unconfigure(self, config):
        if not self.stream.closed:
            self.stream.close()
