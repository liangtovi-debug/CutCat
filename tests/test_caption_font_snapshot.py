"""Byte-preserving standalone fonts and isolated, validated font publication."""
from pathlib import Path
import os
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont, TTCollection
from lumen.model import CaptionStyle, Caption
from lumen import caption_render as captions


def make_font(path, family='Snapshot Fixture', cff=True):
    path.parent.mkdir(parents=True,exist_ok=True)
    fb = FontBuilder(1000, isTTF=not cff)
    names = ['.notdef', 'A', 'B']
    fb.setupGlyphOrder(names); fb.setupCharacterMap({65:'A', 66:'B'})
    fb.setupHorizontalMetrics({n:(600,0) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({'familyName':family,'styleName':'Regular','uniqueFontIdentifier':family,
                      'fullName':family,'psName':family.replace(' ', '')})
    fb.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200)
    fb.setupPost()
    if cff:
        chars={}
        for name in names:
            pen=T2CharStringPen(600,None);pen.moveTo((50,0));pen.lineTo((300,700));pen.lineTo((550,0));pen.closePath()
            chars[name]=pen.getCharString()
        fb.setupCFF(family.replace(' ', ''),{'FullName':family,'FamilyName':family,'Weight':'Regular'},chars,{})
    else:
        glyphs={}
        for name in names:
            pen=TTGlyphPen(None);pen.moveTo((50,0));pen.lineTo((300,700));pen.lineTo((550,0));pen.closePath();glyphs[name]=pen.glyph()
        fb.setupGlyf(glyphs)
    fb.setupMaxp();fb.save(path)
    return path


def identity(path, face=0):
    stat=path.stat()
    return str(path),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,face


@pytest.mark.parametrize('cff', [True,False])
@pytest.mark.parametrize('guarded', [True,False])
def test_standalone_keeps_exact_bytes_without_recompiling(tmp_path, monkeypatch, cff, guarded):
    path=make_font(tmp_path/'fonts/source.otf',cff=cff)
    cache=tmp_path/'cache';cache.mkdir()
    style=CaptionStyle(font_family='Snapshot Fixture',font_directory=str(path.parent))
    def forbidden(*args,**kwargs):raise AssertionError('Standalone font must not be recompiled')
    monkeypatch.setattr(TTFont,'save',forbidden)
    result=captions.prepare_font(style,[Caption('c',0,1,'AB')],cache,check=(lambda:None) if guarded else None)
    assert result.read_bytes()==path.read_bytes()
    assert result.parent==cache
    assert not list(cache.glob('.font-*'))


def test_cached_file_cannot_bypass_glyph_validation_or_return_corruption(tmp_path):
    path=make_font(tmp_path/'fonts/source.otf');cache=tmp_path/'cache';cache.mkdir()
    style=CaptionStyle(font_family='Snapshot Fixture',font_directory=str(path.parent))
    result=captions.prepare_font(style,[Caption('c',0,1,'A')],cache)
    with pytest.raises(captions.CaptionRenderError,match='missing glyphs'):
        captions.prepare_font(style,[Caption('c',0,1,'Z')],cache)
    result.write_bytes(b'corrupt')
    assert captions.prepare_font(style,[Caption('c',0,1,'A')],cache).read_bytes()==path.read_bytes()


def test_snapshot_family_revalidated_even_if_discovery_is_stale(tmp_path,monkeypatch):
    path=make_font(tmp_path/'fonts/source.otf',family='Different Family');cache=tmp_path/'cache';cache.mkdir()
    monkeypatch.setattr(captions,'font_identity',lambda *a,**k:identity(path))
    with pytest.raises(captions.CaptionRenderError,match='family'):
        captions.prepare_font(CaptionStyle(font_family='Snapshot Fixture'),[Caption('c',0,1,'A')],cache)
    assert not list(cache.iterdir())


def test_ttc_magic_extracts_requested_nonzero_face_despite_suffix(tmp_path,monkeypatch):
    first=make_font(tmp_path/'one.ttf',family='Face One',cff=False)
    second=make_font(tmp_path/'two.ttf',family='Face Two',cff=False)
    collection=TTCollection();collection.fonts=[TTFont(first),TTFont(second)]
    path=tmp_path/'collection.otf';collection.save(path);collection.close()
    cache=tmp_path/'cache';cache.mkdir()
    monkeypatch.setattr(captions,'font_identity',lambda *a,**k:identity(path,1))
    result=captions.prepare_font(CaptionStyle(font_family='Face Two'),[Caption('c',0,1,'AB')],cache)
    assert result.read_bytes()[:4]!=b'ttcf'
    with TTFont(result) as font:
        assert font['name'].getDebugName(1)=='Face Two'
        assert set(font.getBestCmap())=={65,66}


def test_content_change_with_same_size_and_mtime_invalidates_cache(tmp_path):
    path=make_font(tmp_path/'fonts/source.ttf',cff=False);cache=tmp_path/'cache';cache.mkdir()
    style=CaptionStyle(font_family='Snapshot Fixture',font_directory=str(path.parent));text=[Caption('c',0,1,'A')]
    before=path.stat();old=captions.prepare_font(style,text,cache)
    with TTFont(path) as font:
        font['hmtx'].metrics['A']=(600,1);font.save(path)
    assert path.stat().st_size==before.st_size
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    new=captions.prepare_font(style,text,cache)
    assert new!=old
    assert new.read_bytes()==path.read_bytes()
    assert new.read_bytes()!=old.read_bytes()


def test_parallel_publication_and_owner_isolation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=make_font(tmp_path/'fonts/source.otf');cache=tmp_path/'cache';cache.mkdir()
    other=tmp_path/'other';other.mkdir()
    style=CaptionStyle(font_family='Snapshot Fixture',font_directory=str(path.parent));text=[Caption('c',0,1,'AB')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:captions.prepare_font(style,text,cache),range(8)))
    assert len(set(results))==1
    independent=captions.prepare_font(style,text,other)
    assert independent!=results[0]
    assert independent.read_bytes()==results[0].read_bytes()==path.read_bytes()
    assert not list(cache.glob('.font-*'))
    if os.name!='nt':assert results[0].stat().st_mode & 0o077==0


def test_cache_symlink_is_rejected_without_touching_target(tmp_path):
    path=make_font(tmp_path/'fonts/source.otf');cache=tmp_path/'cache';cache.mkdir()
    style=CaptionStyle(font_family='Snapshot Fixture',font_directory=str(path.parent));text=[Caption('c',0,1,'A')]
    result=captions.prepare_font(style,text,cache);result.unlink()
    target=tmp_path/'target';target.write_bytes(b'keep')
    try:result.symlink_to(target)
    except OSError:pytest.skip('Host does not permit creating a symlink for this filesystem test')
    with pytest.raises(captions.CaptionRenderError,match='regular'):
        captions.prepare_font(style,text,cache)
    assert target.read_bytes()==b'keep'


@pytest.mark.parametrize('failure',['cancel','replace','fdopen'])
def test_publication_failures_leave_no_partial_file_or_descriptor(tmp_path,monkeypatch,failure):
    path=make_font(tmp_path/'fonts/source.otf');cache=tmp_path/'cache';cache.mkdir()
    style=CaptionStyle(font_family='Snapshot Fixture',font_directory=str(path.parent))
    descriptors=[];original=captions.os.fdopen
    if failure=='fdopen':
        def bad(fd,mode):
            if mode=='wb':descriptors.append(fd);raise OSError('injected fdopen failure')
            return original(fd,mode)
        monkeypatch.setattr(captions.os,'fdopen',bad)
    if failure=='replace':
        monkeypatch.setattr(captions.os,'replace',lambda *a: (_ for _ in ()).throw(OSError('injected replace failure')))
    def check():
        if failure=='cancel' and list(cache.glob('.font-*')):raise RuntimeError('injected cancel')
    with pytest.raises((RuntimeError,OSError),match='injected'):
        captions.prepare_font(style,[Caption('c',0,1,'A')],cache,check=check)
    assert not list(cache.iterdir())
    for fd in descriptors:
        with pytest.raises(OSError):os.fstat(fd)


def test_source_fdopen_failure_closes_descriptor(tmp_path,monkeypatch):
    path=make_font(tmp_path/'fonts/source.otf');seen=[]
    def fail(fd,*args):seen.append(fd);raise OSError('injected source open')
    monkeypatch.setattr(captions.os,'fdopen',fail)
    with pytest.raises(OSError,match='injected'):
        captions._read_font_bytes(path,lambda:None)
    for fd in seen:
        with pytest.raises(OSError):os.fstat(fd)
