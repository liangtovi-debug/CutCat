"""Measured, peak-constrained master-gain drafts. Never mutates a project.

Only ``propose_master_gain`` is GUI-safe. Verification functions read sources
and invoke the bounded, cancellable analysis worker. LUFS after a gain change
is measured again: the BS.1770 absolute gate makes simple prediction unsafe.
No normalization, limiting, project history or persistent caches are involved.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, localcontext
import math

from . import audio_analysis as aa


class LoudnessProposalError(aa.AudioAnalysisError):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class GainProposal:
    current_master_gain_db: float
    proposed_master_gain_db: float
    delta_db: float
    target_lufs: float
    ceiling_dbtp: float
    original_lufs: float
    original_peak_dbtp: float
    peak_limited: bool


@dataclass(frozen=True)
class VerifiedLoudnessProposal:
    proposal: GainProposal
    original_report: aa.AudioAnalysisResult
    draft_report: aa.AudioAnalysisResult
    original_settings_fingerprint: str
    draft_settings_fingerprint: str


def _fail(reason, message):
    raise LoudnessProposalError(reason, message)


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail('nonfinite', f'{label} must be a finite number.')
    return Decimal(str(value))


def _measurement(report):
    if not isinstance(report, aa.AudioAnalysisResult) or report.algorithm != aa.ANALYSIS_ALGORITHM:
        _fail('unsupported', 'The audio report uses an unsupported analysis algorithm.')
    if report.loudness_status != 'measured' or report.integrated_lufs is None:
        _fail('unmeasured', 'A measured loudness report is required; silence, short and below-gate audio cannot be proposed.')
    loudness = _finite(report.integrated_lufs, 'Integrated loudness')
    if loudness < -70 or report.sample_rate != aa.SAMPLE_RATE or type(report.sample_count) is not int or report.sample_count < aa.SAMPLE_RATE * 2 // 5:
        _fail('unsupported', 'The audio report does not contain a supported measured stereo timeline.')
    if (type(report.estimated_true_peak_dbtp) is not tuple or len(report.estimated_true_peak_dbtp) != 2
            or type(report.estimated_true_peaks) is not tuple or len(report.estimated_true_peaks) != 2):
        _fail('unknown_peak', 'The report is missing stereo estimated true peaks.')
    peaks = []
    for amplitude, db in zip(report.estimated_true_peaks, report.estimated_true_peak_dbtp):
        amplitude_decimal = _finite(amplitude, 'Estimated true peak')
        if amplitude_decimal < 0 or (amplitude == 0 and db is not None) or (amplitude > 0 and db is None):
            _fail('unknown_peak', 'The estimated true-peak values are inconsistent.')
        if db is not None:
            peak = _finite(db, 'Estimated true peak dBTP')
            if abs(float(peak) - 20 * math.log10(amplitude)) > 1e-9:
                _fail('unknown_peak', 'The estimated true-peak units are inconsistent.')
            peaks.append(peak)
    if not peaks:
        _fail('unknown_peak', 'A non-silent estimated true peak is required.')
    if (type(report.sample_peaks) is not tuple or len(report.sample_peaks) != 2
            or type(report.sample_peak_dbfs) is not tuple or len(report.sample_peak_dbfs) != 2
            or type(report.overload_counts) is not tuple or len(report.overload_counts) != 2):
        _fail('unsupported', 'The report is missing immutable stereo sample measurements.')
    for amplitude, db, estimated, count in zip(report.sample_peaks, report.sample_peak_dbfs,
                                               report.estimated_true_peaks, report.overload_counts):
        _finite(amplitude, 'Sample peak')
        if (amplitude < 0 or amplitude > estimated or (amplitude == 0 and db is not None)
                or (amplitude > 0 and db is None) or type(count) is not int
                or not 0 <= count <= report.sample_count or (amplitude <= 1 and count != 0)
                or (amplitude > 1 and count == 0)):
            _fail('unsupported', 'The sample-peak and overload measurements are inconsistent.')
        if db is not None:
            _finite(db, 'Sample peak dBFS')
            if abs(db - 20 * math.log10(amplitude)) > 1e-9:
                _fail('unsupported', 'The sample-peak units are inconsistent.')
    if type(report.overload_count) is not int or report.overload_count != sum(report.overload_counts):
        _fail('unsupported', 'The total overload count is inconsistent.')
    if report.overload_count:
        _finite(report.first_overload_seconds, 'First overload time')
        if not 0 <= report.first_overload_seconds < report.sample_count / report.sample_rate:
            _fail('unsupported', 'The first overload time is inconsistent.')
    elif report.first_overload_seconds is not None:
        _fail('unsupported', 'The report has an overload time without overloaded samples.')
    return loudness, max(peaks)


def propose_master_gain(report, current_master_gain_db, target_lufs, ceiling_dbtp):
    """Pure proposal math; NOT evidence of report freshness or safe application.

    Quantize the resulting master gain downward, including negative values.
    An out-of-range unquantized result is rejected, never silently clamped.
    """
    loudness, peak = _measurement(report)
    current = _finite(current_master_gain_db, 'Current master gain')
    target = _finite(target_lufs, 'Target loudness')
    ceiling = _finite(ceiling_dbtp, 'Estimated true-peak ceiling')
    if not -96 <= current <= 24:
        _fail('gain_range', 'Current master gain is outside -96 to +24 dB.')
    # Finite floats can need > 300 decimal digits, even though valid master
    # gains are small. Preserve cancellation before rejecting extreme inputs.
    with localcontext() as context:
        context.prec = 700
        requested_delta, peak_delta = target - loudness, ceiling - peak
        raw = current + min(requested_delta, peak_delta)
        if not -96 <= raw <= 24:
            _fail('gain_range', 'Proposed master gain is outside -96 to +24 dB; it was not clamped.')
        proposed = raw.quantize(Decimal('0.1'), rounding=ROUND_FLOOR)
        if not -96 <= proposed <= 24:
            _fail('gain_range', 'Quantized master gain is outside -96 to +24 dB.')
        return GainProposal(float(current), float(proposed), float(proposed-current),
                            float(target), float(ceiling), float(loudness), float(peak),
                            peak_delta < requested_delta)


def _verify_report(project, report, budget, limits):
    """Rebuild exact report identity using fresh source and decoder hashes."""
    budget.check()
    _measurement(report)
    context = aa.audio_context_fingerprint(project)
    if context != report.context_fingerprint:
        _fail('stale', 'The timeline, media or decoder changed. Analyze again.')
    settings, active = aa._settings(project)
    if len(active) > limits.max_sources or len(project.clips) > limits.max_clips:
        _fail('unsupported', 'The project exceeds the verification limits.')
    if (type(report.source_hashes) is not tuple or len(report.source_hashes) > limits.max_sources
            or any(type(row) is not tuple or len(row) != 2
                   or not all(isinstance(value, str) and len(value) <= 256 for value in row)
                   for row in report.source_hashes)
            or not isinstance(report.decoder_version, str) or not 1 <= len(report.decoder_version) <= limits.max_probe_bytes
            or any(not isinstance(value, str) or len(value) != 64
                   for value in (report.fingerprint, report.context_fingerprint))):
        _fail('unsupported', 'The report identity is malformed or exceeds verification limits.')
    hashes = []
    total = 0
    for key, item in sorted(active.items()):
        budget.check()
        path = aa._local_path(item.path)
        total += path.stat().st_size
        if total > limits.max_total_source_bytes:
            _fail('unsupported', 'Sources exceed the verification size limit.')
        hashes.append((key, aa._copy_hash(path, None, budget, limits.max_source_bytes)))
    if tuple(hashes) != report.source_hashes:
        _fail('stale', 'Audio source contents no longer match the measured report.')
    digest, version = aa._decoder_identity(aa.ffmpeg_binary(), budget, limits)
    if (version != report.decoder_version
            or aa._hash_json([settings, hashes, digest, version]) != report.fingerprint
            or aa.audio_context_fingerprint(project) != context):
        _fail('stale', 'The report fingerprint or analysis pipeline no longer matches.')
    budget.check()


def _draft(project, proposal):
    detached = deepcopy(project)
    detached.master_gain_db = proposal.proposed_master_gain_db
    detached.validate()
    return detached


def _validate_pair(project, verified, budget, limits):
    if not isinstance(verified, VerifiedLoudnessProposal):
        _fail('unsupported', 'A verified loudness proposal is required.')
    p = verified.proposal
    if aa.audio_settings_fingerprint(project) != verified.original_settings_fingerprint:
        _fail('stale', 'The timeline changed after the gain proposal was measured.')
    expected = propose_master_gain(verified.original_report, project.master_gain_db,
                                   p.target_lufs, p.ceiling_dbtp)
    if p != expected:
        _fail('stale', 'The proposed gain no longer matches the original report.')
    detached = _draft(project, p)
    if aa.audio_settings_fingerprint(detached) != verified.draft_settings_fingerprint:
        _fail('stale', 'The draft settings do not match the proposed gain.')
    _verify_report(project, verified.original_report, budget, limits)
    _verify_report(detached, verified.draft_report, budget, limits)
    if (verified.draft_report.source_hashes != verified.original_report.source_hashes
            or verified.draft_report.decoder_version != verified.original_report.decoder_version
            or verified.draft_report.sample_count != verified.original_report.sample_count):
        _fail('stale', 'Draft sources or analysis pipeline differ from the original report.')
    _, peak = _measurement(verified.draft_report)
    if peak > Decimal(str(p.ceiling_dbtp)):
        _fail('peak_ceiling', 'The measured draft exceeds the estimated true-peak ceiling.')
    budget.check()


def validate_verified_proposal(project, verified, cancel=None, *, limits=None):
    """Worker-only pre-apply freshness check. Never changes project or history.

    The GUI must additionally check its in-memory settings/generation at the
    moment of commit; external source changes can occur after any worker check.
    """
    limits = limits or aa.AnalysisLimits()
    _validate_pair(project, verified, aa._Budget(limits, cancel), limits)


def verify_loudness_proposal(project, report, target_lufs, ceiling_dbtp,
                             cancel=None, *, limits=None, progress=None):
    """Fresh baseline -> detached actual measurement -> fresh final verification."""
    limits = limits or aa.AnalysisLimits()
    budget = aa._Budget(limits, cancel)
    budget.check()
    owned = deepcopy(project)
    owned.validate()
    original_key = aa.audio_settings_fingerprint(owned)
    proposal = propose_master_gain(report, owned.master_gain_db, target_lufs, ceiling_dbtp)
    if progress:
        progress('baseline_verify')
    _verify_report(owned, report, budget, limits)
    if aa.audio_settings_fingerprint(project) != original_key:
        _fail('stale', 'The timeline changed before draft analysis.')
    detached = _draft(owned, proposal)
    draft_key = aa.audio_settings_fingerprint(detached)
    # Include hash verification and decode in one overall cancellable budget.
    # The analyzer checks this callback throughout subprocesses and file reads.
    def cancelled():
        budget.check()
        return False
    draft_report = aa.analyze_audio(detached, cancelled, limits=limits, progress=progress)
    verified = VerifiedLoudnessProposal(proposal, report, draft_report, original_key, draft_key)
    if progress:
        progress('proposal_verify')
    _validate_pair(owned, verified, budget, limits)
    _verify_report(project, report, budget, limits)
    budget.check()
    return verified
