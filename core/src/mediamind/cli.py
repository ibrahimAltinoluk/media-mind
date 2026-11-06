"""mediamind komut satırı."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from . import analyze as analyze_mod
from . import dedup, organize, plans, registry, scan as scan_mod
from .paths import fmt_int, human_bytes
from .repo import Repo, RepoError

app = typer.Typer(help="MediaMind: fotoğraf ve video arşivi yöneticisi", no_args_is_help=True, add_completion=False)
plan_app = typer.Typer(help="Planları listele, göster, uygula, geri al", no_args_is_help=True)
trash_app = typer.Typer(help="Repo çöp kutusu", no_args_is_help=True)
repos_app = typer.Typer(help="Kayıtlı repolar", no_args_is_help=True)
app.add_typer(plan_app, name="plan")
app.add_typer(trash_app, name="trash")
app.add_typer(repos_app, name="repos")

console = Console()
err = Console(stderr=True)

RepoOpt = typer.Option(None, "--repo", "-r", help="Repo yolu, adı ya da kimliği (varsayılan: bulunulan klasör)")

STAGE_LABELS = {"walk": "Dosyalar listeleniyor", "hash": "İçerik hash'leniyor", "meta": "Meta veri", "geo": "Konum",
                "thumbs": "Önizleme ve algısal hash", "apply": "Uygulanıyor", "undo": "Geri alınıyor"}


def _open(ref: Optional[str]) -> Repo:
    try:
        return Repo.open(ref)
    except RepoError as e:
        err.print(f"[red]Hata:[/] {e}")
        raise typer.Exit(1)


class _Bar:
    def __init__(self):
        self.p = Progress(TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), TimeRemainingColumn(),
                          console=err, transient=True)
        self.tasks: dict[str, int] = {}

    def __enter__(self):
        self.p.start()
        return self

    def __exit__(self, *a):
        self.p.stop()

    def __call__(self, stage: str, done: int, total: int):
        if stage not in self.tasks:
            self.tasks[stage] = self.p.add_task(STAGE_LABELS.get(stage, stage), total=total or None)
        self.p.update(self.tasks[stage], completed=done, total=total or None)


def _lines(title: str, items: list[str], style: str, limit: int) -> None:
    if not items:
        return
    console.print(f"\n[bold]{title}[/] ({fmt_int(len(items))})")
    for s in items[:limit]:
        console.print(f"  [{style}]{s}[/]")
    if len(items) > limit:
        console.print(f"  [dim]… ve {fmt_int(len(items) - limit)} tane daha[/]")


# ---------------------------------------------------------------------------- repo
@app.command()
def init(path: Path = typer.Argument(Path("."), help="Repo yapılacak klasör"),
         name: Optional[str] = typer.Option(None, "--name", "-n", help="Repo adı")):
    """Bir klasörü MediaMind reposu yapar (.mediamind/ oluşturur, dosyalara dokunmaz)."""
    try:
        repo = Repo.init(path, name)
    except RepoError as e:
        err.print(f"[red]Hata:[/] {e}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] '{repo.name}' reposu oluşturuldu: {repo.root}")
    console.print("  Sonraki adım: [bold]mediamind status[/] ile dosyaları tarayın.")


@app.command()
def status(repo_ref: Optional[str] = RepoOpt, limit: int = typer.Option(20, help="Her listede gösterilecek en fazla satır"),
           short: bool = typer.Option(False, "--short", "-s", help="Yalnızca özet")):
    """Repoyu tarar ve git status gibi değişiklikleri gösterir."""
    repo = _open(repo_ref)
    with _Bar() as bar:
        rep = scan_mod.scan(repo, bar)
    c = scan_mod.counts(repo)
    console.print(f"[bold]{repo.name}[/]  [dim]{repo.root}[/]")
    console.print(
        f"Takipte: [green]{fmt_int(c.tracked)}[/] · Takipsiz: [cyan]{fmt_int(c.untracked)}[/] · "
        f"Eksik: [red]{fmt_int(c.missing)}[/] · Çöpte: {fmt_int(c.trashed)} · "
        f"{fmt_int(c.photos)} fotoğraf, {fmt_int(c.videos)} video, {human_bytes(c.bytes)}"
    )
    if short:
        return
    _lines("Bu taramada taşınmış", [f"{a} → {b}" for a, b in rep.moved], "yellow", limit)
    _lines("Bu taramada değişmiş", rep.modified, "yellow", limit)
    _lines("Geri gelen", rep.reappeared, "green", limit)
    untracked = [r["rel_path"] for r in repo.db.execute(
        "SELECT rel_path FROM files WHERE state='present' AND tracked=0 ORDER BY rel_path")]
    copy_of = dict(rep.copies)
    _lines("Takipsiz dosyalar", [f"{u}  [dim](kopya: {copy_of[u]})[/]" if u in copy_of else u for u in untracked], "cyan", limit)
    missing = [r["rel_path"] for r in repo.db.execute("SELECT rel_path FROM files WHERE state='missing' ORDER BY rel_path")]
    _lines("Eksik dosyalar", missing, "red", limit)
    if untracked:
        console.print("\n  Takibe almak için: [bold]mediamind track[/] (ya da [bold]mediamind track <klasör>[/])")
    if missing:
        console.print("  Eksikleri unutmak için: [bold]mediamind forget-missing[/]")


@app.command()
def scan(repo_ref: Optional[str] = RepoOpt):
    """Sessiz tarama: değişiklikleri DB'ye işler, kısa özet yazar."""
    repo = _open(repo_ref)
    with _Bar() as bar:
        rep = scan_mod.scan(repo, bar)
    console.print(
        f"yeni {len(rep.new)} · kopya {len(rep.copies)} · taşınmış {len(rep.moved)} · "
        f"değişmiş {len(rep.modified)} · eksik {len(rep.missing)} · geri gelen {len(rep.reappeared)}"
    )


@app.command()
def track(paths: Optional[list[str]] = typer.Argument(None, help="Dosya ya da klasör yolları (boşsa tüm takipsizler)"),
          repo_ref: Optional[str] = RepoOpt,
          no_analyze: bool = typer.Option(False, "--no-analyze", help="Yalnızca takibe al, analiz etme")):
    """Takipsiz dosyaları takibe alır ve analiz eder."""
    repo = _open(repo_ref)
    rels = None
    if paths:
        rels = []
        for p in paths:
            ap = Path(p).expanduser().resolve()
            rels.append(ap.relative_to(repo.root).as_posix() if ap.is_relative_to(repo.root) else p)
    n = analyze_mod.track(repo, rels)
    console.print(f"[green]✓[/] {fmt_int(n)} dosya takibe alındı.")
    if not no_analyze:
        _run_analyze(repo, False)


def _run_analyze(repo: Repo, force: bool) -> None:
    with _Bar() as bar:
        res = analyze_mod.analyze(repo, bar, force)
    errs = repo.db.execute("SELECT COUNT(*) FROM assets WHERE analysis_state LIKE '%\"errors\"%'").fetchone()[0]
    console.print(f"[green]✓[/] Analiz: meta {res['meta']} · konum {res['geo']} · önizleme {res['thumbs']}"
                  + (f" · [yellow]{errs} dosyada hata[/]" if errs else ""))


@app.command()
def analyze(repo_ref: Optional[str] = RepoOpt, force: bool = typer.Option(False, help="Tümünü yeniden analiz et")):
    """Takipteki dosyalarda eksik analiz aşamalarını çalıştırır."""
    _run_analyze(_open(repo_ref), force)


@app.command("forget-missing")
def forget_missing(repo_ref: Optional[str] = RepoOpt):
    """Diskte bulunmayan dosyaları kayıtlardan siler."""
    n = scan_mod.forget_missing(_open(repo_ref))
    console.print(f"{fmt_int(n)} eksik kayıt silindi.")


# ---------------------------------------------------------------------------- tekrarlar
@app.command()
def dups(repo_ref: Optional[str] = RepoOpt,
         kind: Optional[list[str]] = typer.Option(None, "--kind", "-k", help="exact | near | video"),
         threshold: Optional[int] = typer.Option(None, help="pHash Hamming eşiği (varsayılan repo ayarı, 6)"),
         rule: Optional[str] = typer.Option(None, help="quality | oldest | primary_folder"),
         limit: int = typer.Option(20, help="Gösterilecek en fazla grup"),
         make_plan: bool = typer.Option(False, "--plan", help="Silinmesi önerilenler için çöp planı oluştur")):
    """Tekrarlı dosyaları bulur ve hangisinin tutulacağını önerir."""
    repo = _open(repo_ref)
    groups = dedup.find(repo, threshold, rule)
    kinds = set(kind) if kind else None
    shown = [g for g in groups if not kinds or g.kind in kinds]
    labels = {"exact": "Birebir aynı", "near": "Neredeyse aynı", "video": "Video kopyası"}
    total_gain = sum(g.gain_bytes for g in shown)
    console.print(f"[bold]{len(shown)} grup[/] · silinirse {human_bytes(total_gain)} boşalır")
    for g in shown[:limit]:
        t = Table(title=f"Grup {g.id} · {labels[g.kind]}", title_justify="left", show_header=False, box=None, pad_edge=False)
        for m in g.members:
            mark = "[green]TUT[/]" if m.role == "keep" else "[red]SİL[/]"
            res = f"{m.width}×{m.height}" if m.width else "?"
            t.add_row(mark, m.rel_path, res, human_bytes(m.size), f"[dim]{m.reason}[/]")
        console.print(t)
    if len(shown) > limit:
        console.print(f"[dim]… ve {len(shown) - limit} grup daha[/]")
    if make_plan:
        items = dedup.plan_items(groups, kinds)
        if not items:
            console.print("Silinecek öneri yok.")
            return
        pid = plans.create(repo, "dedup", items, title="Tekrarları çöpe taşı")
        _print_plan(repo, pid, 10)


# ---------------------------------------------------------------------------- düzenleme
@app.command("organize")
def organize_cmd(template: str = typer.Argument(..., help="Şablon ya da hazır ad: " + ", ".join(organize.PRESETS)),
                 repo_ref: Optional[str] = RepoOpt,
                 scope: str = typer.Option("tracked", help="tracked | untracked | all"),
                 under: Optional[str] = typer.Option(None, help="Yalnız bu klasörün altındakiler"),
                 base: str = typer.Option("", help="Hedef kök klasör (ör. Arsiv)"),
                 limit: int = typer.Option(15, help="Provada gösterilecek satır")):
    """Dosyaları şablona göre yerleştiren bir taşıma planı oluşturur (uygulamaz)."""
    repo = _open(repo_ref)
    try:
        pid = organize.build_plan(repo, template, scope, under, base)
    except organize.TemplateError as e:
        err.print(f"[red]Şablon hatası:[/] {e}")
        raise typer.Exit(1)
    _print_plan(repo, pid, limit)


# ---------------------------------------------------------------------------- planlar
def _print_plan(repo: Repo, pid: int, limit: int = 20) -> None:
    p = plans.get(repo, pid)
    s = json.loads(p["summary"])
    its = plans.items(repo, pid)
    if p["status"] == "rejected" and not its:
        console.print("Yapılacak bir değişiklik yok.")
        return
    console.print(f"\n[bold]Plan #{pid}[/] · {p['title'] or p['kind']} · [yellow]{p['status']}[/]")
    console.print(f"  {fmt_int(s.get('files', 0))} dosya ({fmt_int(s.get('items', 0))} öğe, yan dosyalar dahil) · "
                  f"{human_bytes(s.get('bytes', 0))}" + (f" · {s['renamed']} ad çakışması yeniden adlandırıldı" if s.get("renamed") else ""))
    for it in its[:limit]:
        arrow = "→ çöp" if it["op"] == "trash" else f"→ {it['dst']}"
        st = "" if it["status"] == "pending" else f" [dim]({it['status']})[/]"
        console.print(f"  {it['src']} [yellow]{arrow}[/]{st}")
    if len(its) > limit:
        console.print(f"  [dim]… ve {len(its) - limit} öğe daha[/]")
    if p["status"] == "pending":
        console.print(f"\n  Uygulamak için: [bold]mediamind plan apply {pid}[/]   Reddetmek için: [bold]mediamind plan reject {pid}[/]")


@plan_app.command("list")
def plan_list(repo_ref: Optional[str] = RepoOpt):
    repo = _open(repo_ref)
    t = Table("No", "Tür", "Başlık", "Kaynak", "Durum", "Dosya", "Oluşturma")
    for p in plans.list_plans(repo):
        s = json.loads(p["summary"])
        t.add_row(str(p["id"]), p["kind"], p["title"] or "", p["source"], p["status"], fmt_int(s.get("files", 0)), p["created_at"])
    console.print(t)


@plan_app.command("show")
def plan_show(plan_id: int, repo_ref: Optional[str] = RepoOpt, limit: int = 50):
    repo = _open(repo_ref)
    try:
        _print_plan(repo, plan_id, limit)
    except plans.PlanError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1)


@plan_app.command("apply")
def plan_apply(plan_id: int, repo_ref: Optional[str] = RepoOpt, yes: bool = typer.Option(False, "--yes", "-y", help="Onay sorma")):
    repo = _open(repo_ref)
    try:
        _print_plan(repo, plan_id, 10)
        if not yes and not typer.confirm("Bu plan uygulansın mı?"):
            raise typer.Exit(0)
        with _Bar() as bar:
            res = plans.apply(repo, plan_id, bar)
    except plans.PlanError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] Plan #{plan_id}: {res.done} tamam · {res.skipped} atlandı · {res.failed} başarısız ({res.status})")
    console.print(f"  Geri almak için: [bold]mediamind plan undo {plan_id}[/]")


@plan_app.command("undo")
def plan_undo(plan_id: int, repo_ref: Optional[str] = RepoOpt):
    repo = _open(repo_ref)
    try:
        with _Bar() as bar:
            res = plans.undo(repo, plan_id, bar)
    except plans.PlanError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] Plan #{plan_id} geri alındı: {res.done} tamam · {res.skipped} atlandı · {res.failed} başarısız")


@plan_app.command("reject")
def plan_reject(plan_id: int, repo_ref: Optional[str] = RepoOpt):
    try:
        plans.reject(_open(repo_ref), plan_id)
    except plans.PlanError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"Plan #{plan_id} reddedildi.")


# ---------------------------------------------------------------------------- çöp
@trash_app.command("list")
def trash_list(repo_ref: Optional[str] = RepoOpt):
    repo = _open(repo_ref)
    rows = plans.trash_contents(repo)
    total = sum(r["size"] for r in rows)
    console.print(f"Çöpte {fmt_int(len(rows))} dosya · {human_bytes(total)}")
    for r in rows[:50]:
        console.print(f"  #{r['plan_id']}  {r['src']}  [dim]{r['applied_at']}[/]")


@trash_app.command("empty")
def trash_empty(repo_ref: Optional[str] = RepoOpt,
                older_than: Optional[int] = typer.Option(None, "--older-than", help="Yalnız N günden eski olanlar"),
                yes: bool = typer.Option(False, "--yes", "-y")):
    """Çöpü KALICI olarak boşaltır (geri alınamaz)."""
    repo = _open(repo_ref)
    rows = plans.trash_contents(repo, older_than)
    if not rows:
        console.print("Çöp boş.")
        return
    if not yes and not typer.confirm(f"{len(rows)} dosya kalıcı olarak silinecek. Emin misiniz?"):
        raise typer.Exit(0)
    n, freed = plans.empty_trash(repo, older_than)
    console.print(f"[green]✓[/] {fmt_int(n)} dosya silindi, {human_bytes(freed)} boşaldı.")


# ---------------------------------------------------------------------------- repolar
@repos_app.command("list")
def repos_list():
    t = Table("Ad", "Yol", "Durum", "Kimlik")
    for e in registry.load():
        t.add_row(e.name, e.last_path, "[green]bağlı[/]" if e.online else "[dim]çevrimdışı[/]", e.id[:8])
    console.print(t)


@repos_app.command("add")
def repos_add(path: Path):
    """Var olan bir repoyu menüye/listeye ekler."""
    try:
        repo = Repo.discover(path)
    except RepoError as e:
        err.print(f"[red]Hata:[/] {e}")
        raise typer.Exit(1)
    registry.register(repo.id, repo.name, repo.root)
    console.print(f"[green]✓[/] '{repo.name}' listeye eklendi.")


@repos_app.command("remove")
def repos_remove(ref: str):
    """Repoyu listeden kaldırır. Dosyalara ve .mediamind/ klasörüne dokunmaz."""
    e = registry.unregister(ref)
    if e is None:
        err.print(f"[red]Repo bulunamadı:[/] {ref}")
        raise typer.Exit(1)
    console.print(f"'{e.name}' listeden kaldırıldı (dosyalar yerinde).")


@app.command()
def mcp():
    """MCP sunucusunu stdio üzerinden başlatır (Claude Desktop / Claude Code için)."""
    from .mcp_server import main as _mcp_main

    _mcp_main()


@app.command()
def serve(port: int = typer.Option(7341, help="Port"),
          token: Optional[str] = typer.Option(None, envvar="MEDIAMIND_TOKEN", help="İstemcilerin göndermesi gereken oturum token'ı")):
    """Arayüz için yerel HTTP API'yi başlatır (yalnızca 127.0.0.1)."""
    from .api.server import serve as _serve

    err.print(f"MediaMind API http://127.0.0.1:{port}")
    _serve(port=port, token=token)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
