# Live migration dan pengosongan node compute - rencana implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /servers/{ref}/live-migrate` memindahkan server ACTIVE ke node compute lain tanpa mematikannya, dan `cloudx host drain NODE` memakainya untuk mengosongkan sebuah node.

**Architecture:** compute-api memeriksa kelayakan, memindahkan klaim placement, dan mencast `live_migrate` ke worker sumber. Worker sumber mengatur seluruh alur: `rpc.call` persiapan ke worker tujuan, migrasi libvirt `qemu+ssh` di thread sendiri, lalu `finish` atau `rollback`. Pengosongan node ada di CLI (tanpa status di server).

**Tech Stack:** Python 3.12, FastAPI, libvirt-python (`migrateToURI3`), RabbitMQ RPC (`shared.rpc`), click, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-live-migration-design.md`

## Global Constraints

- Kebijakan baru `compute:server:live_migrate`, aturan bawaan `role:admin`. Jumlah aksi bawaan 57 menjadi 58 (yang baru: 35 menjadi 36).
- Jawaban 202: `{"server_id", "from", "to", "status": "MIGRATING"}`. Penolakan 409 menyebut alasannya; server lain proyek = 404.
- Flag libvirt: `LIVE | PEER2PEER | PERSIST_DEST | UNDEFINE_SOURCE | ABORT_ON_ERROR`, ditambah `AUTO_CONVERGE` hanya bila `live_migration_auto_converge` true; parameter `DEST_XML`.
- Konfigurasi `compute-worker.conf` `[DEFAULT]`: `live_migration_host` (bawaan `COMPUTE_HOST`), `live_migration_uri` (bawaan `qemu+ssh://root@{host}/system`), `live_migration_timeout` 1800, `live_migration_max_downtime_ms` 300, `live_migration_bandwidth_mbps` 0, `live_migration_auto_converge` false.
- Port antar compute node: 49152-49215/tcp (data QEMU) dan 22/tcp.
- Tidak ada perubahan skema. Status server: `ACTIVE` -> `MIGRATING` -> `ACTIVE` (host baru) atau `ACTIVE` (host lama) tanpa `error_message`.
- Tap di node tujuan dipasang ke `br-int` tanpa `iface-id` sampai `finish`; node sumber menghapus tap dan port OVS-nya sebelum `finish`.
- Pengosongan: server dengan LV yang `host`-nya node itu dilewati dengan alasan; `SHUTOFF` memakai cold migrate; node dibiarkan `disabled` (`disabled_reason="drain"`).
- Cabang `feat/live-migration`; satu commit per task; akhiri pesan commit dengan `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Tes dijalankan dengan `python -m pytest` dari akar repo (venv scratch yang sudah berisi `requirements-dev.txt`).

## Review Focus

1. Dua `live-migrate` pada server yang sama berurutan cepat: yang kedua melihat `MIGRATING` dan dijawab 409, tidak membuat klaim kedua. (Task 6)
2. Persiapan di tujuan diulang setelah worker sumber mati di tengah (tap dan sesi iSCSI sisa): persiapan kedua berhasil tanpa galat. (Task 4)
3. Worker sumber restart saat server `MIGRATING`: domain masih jalan lokal berarti server kembali `ACTIVE`; domain tidak ada berarti tidak disentuh. (Task 2)
4. Volume terpasang dengan `path` kosong atau status bukan `in-use`: 409 sebelum apa pun berubah. (Task 6)
5. `drain` pada node tanpa server, atau semua server dilewati: keluaran jelas, kode keluar 0 untuk node kosong dan bukan nol bila ada yang dilewati atau gagal. (Task 7)

---

### Task 1: Kebijakan `compute:server:live_migrate`

**Files:**
- Modify: `services/shared/policy.py` (tambahkan ke `_NEW_ADMIN_ACTIONS`, setelah `compute:server:migrate`)
- Modify: `etc/cloudx/policy.yaml.sample`, `docs/policy.md` (baris tabel, jumlah 57 menjadi 58 dan 35 menjadi 36 di teks)
- Test: `tests/shared/test_policy.py`, `tests/shared/test_policy_cli.py`, `tests/shared/test_policy_coverage.py`, `tests/shared/test_policy_engine_pr2.py` (angka 57 dan 35 ikut berubah)

**Interfaces:**
- Produces: aksi `compute:server:live_migrate` dengan aturan `role:admin`, dipakai `policy.require("compute:server:live_migrate")` di Task 6.

- [ ] **Step 1: Ubah tes** yang menyebut 57 aksi menjadi 58 (dan 35 baru menjadi 36); tambahkan di `test_policy.py` `test_live_migrate_is_an_admin_action`: `policy.DEFAULT_RULES["compute:server:live_migrate"] == "role:admin"`.
- [ ] **Step 2: Jalankan** `python -m pytest tests/shared -q`; harapannya gagal di tes yang diubah.
- [ ] **Step 3: Tambahkan aksi** di `services/shared/policy.py`, baris di `etc/cloudx/policy.yaml.sample` (`# compute:server:live_migrate: "role:admin"`), baris tabel `POST /servers/{ref}/live-migrate` di `docs/policy.md`, dan perbarui angka di teks dokumen itu.
- [ ] **Step 4: Jalankan** `python -m pytest tests/shared tests/docs -q`; harapannya lulus.
- [ ] **Step 5: Commit** `feat(policy): compute:server:live_migrate`.

### Task 2: Rekonsiliator melewati `MIGRATING`, dan pemulihan saat worker start

**Files:**
- Modify: `services/compute_worker/app/main.py` (`reconcile_stale_domains`; fungsi baru `recover_migrating_servers`; dipanggil di `main()` sebelum konsumer jalan)
- Test: `tests/compute_worker/test_migrating_recovery.py`

**Interfaces:**
- Produces: `recover_migrating_servers() -> None` di `main.py`. Mengambil server dari `GET {compute}/internal/servers` (sama dengan rekonsiliator); untuk server `status == "MIGRATING"` dan `host == COMPUTE_HOST` yang domainnya (`libvirt_domain_name`) ada di `libvirt_driver.get_all_domain_states()` dengan status `RUNNING`, memanggil `conductor_update_server(id, {"status": "ACTIVE", "error_message": None})`. Tidak menyentuh server lain. Tidak pernah melempar.

- [ ] **Step 1: Tulis tes** (libvirt dan HTTP dipalsukan lewat `monkeypatch` pada `main.libvirt_driver.get_all_domain_states`, `main.requests.get`, `main.conductor_update_server`):
  - `test_reconcile_skips_a_domain_whose_server_is_migrating`: server `MIGRATING` dengan `host="node-b"` dan domain ada di node ini (`COMPUTE_HOST="node-a"`): `libvirt_driver.delete` tidak dipanggil.
  - `test_reconcile_still_destroys_a_stale_active_domain`: sama tetapi `ACTIVE`: `delete` dipanggil.
  - `test_recover_returns_a_running_migrating_server_to_active`: `MIGRATING`, `host="node-a"`, domain `RUNNING`: tepat satu `conductor_update_server(id, {"status": "ACTIVE", "error_message": None})`.
  - `test_recover_leaves_a_server_whose_domain_is_not_here`: `MIGRATING`, `host="node-a"`, domain tidak ada: tidak ada pembaruan.
  - `test_recover_ignores_servers_of_other_hosts_and_never_raises`: server `MIGRATING` di `node-b` tidak disentuh; HTTP melempar: fungsi kembali tanpa galat.
- [ ] **Step 2: Jalankan** `python -m pytest tests/compute_worker/test_migrating_recovery.py -q`; gagal.
- [ ] **Step 3: Implementasikan.** Di `reconcile_stale_domains` bangun himpunan domain milik server `MIGRATING` dan lewati domain itu di loop. Tulis `recover_migrating_servers` dan panggil sekali di `main()` setelah koneksi RabbitMQ siap.
- [ ] **Step 4: Jalankan** `python -m pytest tests/compute_worker -q`; lulus.
- [ ] **Step 5: Commit** `fix(compute-worker): the stale-domain reconciler skips migrating servers; a restart frees a running migrating server`.

### Task 3: Primitif libvirt dan jaringan

**Files:**
- Modify: `services/compute_worker/app/libvirt_driver.py`, `services/compute_worker/app/network_manager.py`, `services/compute_worker/app/config.py`
- Test: `tests/compute_worker/test_live_migration_primitives.py`

**Interfaces:**
- Produces (`libvirt_driver.py`):
  - `rewrite_disk_sources(xml: str, mapping: dict[str, str]) -> str`: untuk tiap `<disk>` yang `<source dev=...>` atau `<source file=...>`-nya ada sebagai kunci di `mapping`, ganti dengan nilainya; sisanya (termasuk disk RBD `type='network'`) tidak diubah.
  - `class MigrationError(RuntimeError)`.
  - `migrate_domain(name: str, dest_uri: str, dest_xml: str, *, timeout: int, bandwidth_mbps: int, max_downtime_ms: int, auto_converge: bool) -> None`: jalankan `migrateToURI3` di thread, pantau `domain.jobInfo()` tiap detik, `abortJob()` dan `MigrationError("timed out after N s")` bila lewat `timeout`, `MigrationError(str(exc))` bila libvirt gagal. `migrateSetMaxDowntime(max_downtime_ms)` dipanggil setelah migrasi mulai.
  - `domain_xml_for_migration(name: str) -> str`: `XMLDesc(VIR_DOMAIN_XML_MIGRATABLE)`.
- Produces (`network_manager.py`): `plug_tap(name: str) -> None` (hanya `ovs-vsctl --may-exist add-port OVS_BRIDGE name`); `set_tap_binding(name, logical_port_name, mac_address, server_id) -> None` (empat `external_ids` yang sekarang ada di `bind_tap_to_ovs`); `bind_tap_to_ovs` memanggil keduanya dan perilakunya tidak berubah.
- Produces (`config.py`): konstanta `LIVE_MIGRATION_HOST` (bawaan `COMPUTE_HOST`), `LIVE_MIGRATION_URI`, `LIVE_MIGRATION_TIMEOUT` (int), `LIVE_MIGRATION_MAX_DOWNTIME_MS` (int), `LIVE_MIGRATION_BANDWIDTH_MBPS` (int), `LIVE_MIGRATION_AUTO_CONVERGE` (bool; "true", "1", "yes" tanpa memandang huruf) dari `os.getenv` dengan bawaan di Global Constraints.

- [ ] **Step 1: Tulis tes.**
  - `test_rewrite_changes_only_listed_block_and_file_sources`: XML dengan disk blok `/dev/vg/v1`, disk iSCSI, cdrom file, dan disk `type='network'` RBD; mapping `{"/dev/vg/v1": "/dev/disk/by-path/ip-1.2.3.4:3260-iscsi-iqn.x-lun-0"}`: hanya sumber itu berubah, elemen lain identik.
  - `test_migrate_domain_aborts_after_the_timeout`: `domain.migrateToURI3` palsu yang tidur; `timeout=1`: `abortJob` dipanggil, `MigrationError` bermakna "timed out".
  - `test_migrate_domain_passes_flags_params_and_options`: tanpa `auto_converge` flag `VIR_MIGRATE_AUTO_CONVERGE` tidak ada, dengan `auto_converge=True` ada; flag `LIVE|PEER2PEER|PERSIST_DEST|UNDEFINE_SOURCE|ABORT_ON_ERROR` selalu ada; params berisi `VIR_MIGRATE_PARAM_DEST_XML`, dan `VIR_MIGRATE_PARAM_BANDWIDTH` hanya bila `bandwidth_mbps > 0`.
  - `test_migrate_domain_wraps_a_libvirt_error`: `migrateToURI3` melempar: `MigrationError` membawa teks galat.
  - `test_plug_tap_sets_no_iface_id` dan `test_bind_tap_to_ovs_is_unchanged`: `run` dipalsukan; `plug_tap` hanya satu `add-port`; `bind_tap_to_ovs` menghasilkan lima perintah yang sama dengan sebelumnya, berurutan.
  - `test_live_migration_settings_default_and_override` (muat ulang `config` dengan `importlib.reload`, pola `reloaded_config` di `tests/compute_worker/test_console_info.py`).
- [ ] **Step 2: Jalankan** `python -m pytest tests/compute_worker/test_live_migration_primitives.py -q`; gagal.
- [ ] **Step 3: Implementasikan** sesuai Interfaces. `rewrite_disk_sources` memakai `xml.etree.ElementTree`; `migrate_domain` meng-impor `libvirt` di dalam fungsi seperti fungsi libvirt lain di file itu dan menjalankan `migrateToURI3` di `threading.Thread`.
- [ ] **Step 4: Jalankan** `python -m pytest tests/compute_worker -q`; lulus.
- [ ] **Step 5: Commit** `feat(compute-worker): libvirt and OVS primitives for live migration`.

### Task 4: Sisi tujuan (`prepare`, `finish`, `rollback`)

**Files:**
- Create: `services/compute_worker/app/live_migration.py`
- Modify: `services/compute_worker/app/main.py` (handler `handle_live_migrate_prepare`, `handle_live_migrate_finish`, `handle_live_migrate_rollback`; daftarkan sebagai `"live_migrate_prepare"`, `"live_migrate_finish"`, `"live_migrate_rollback"` di `HANDLERS` pada bagian Call)
- Test: `tests/compute_worker/test_live_migration_target.py`

**Interfaces:**
- Consumes: `network_manager.create_tap/plug_tap/set_tap_binding/delete_tap/port_tap_name/tap_name`, `volume_manager.connect(volume, rpc) -> Path`, `volume_manager.disconnect(volume_id, host, rpc)`, `volume_manager._is_rbd`, `cloudinit.create_config_drive`, `worker.expose_to_host_libvirt`, `worker._nics_of`, `config.LIVE_MIGRATION_HOST`.
- Payload (`args`) yang sama dengan payload build (`server_worker_payload` dengan `port`, `ports`) ditambah `_volumes: list[dict]` berisi tiap volume `{"volume_id", "host", "path", "backend", "target_dev"}` (volume root `target_dev="sda"`) dan `_source_host: str`.
- Produces di `live_migration.py`:
  - `prepare(args: dict, rpc) -> dict` mengembalikan `{"disk_paths": {volume_id: path_di_tujuan}, "migration_host": LIVE_MIGRATION_HOST}`. `path_di_tujuan` = path asli untuk RBD atau LV yang `host` node ini; `str(volume_manager.connect(...))` untuk LV di node lain. Idempoten: tap atau sesi yang sudah ada tidak membuat galat. Bila langkah mana pun gagal, hal yang dibuat oleh panggilan ini dibersihkan lalu galat dilempar.
  - `finish(args: dict) -> dict` memanggil `network_manager.set_tap_binding` untuk tap utama dan tiap tap tambahan (`logical_port_name` dari `port.get("logical_port_name") or port.get("name")`), mengembalikan `{"status": "ok"}`.
  - `rollback(args: dict, rpc) -> dict` menghapus tap (`delete_tap`), `volume_manager.disconnect` untuk tiap volume LV yang `host` bukan node ini, menghapus `instance_dir` tujuan, dan `libvirt_driver.delete(libvirt_domain_name)` bila domain ada; semua best-effort, selalu mengembalikan `{"status": "ok"}`.

- [ ] **Step 1: Tulis tes** dengan palsu untuk `network_manager`, `volume_manager`, `cloudinit`, `libvirt_driver`:
  - `test_prepare_plugs_taps_without_binding_and_returns_disk_paths`: dua port; `plug_tap` dan `create_tap` dipanggil per tap, `set_tap_binding` tidak; `disk_paths` untuk RBD tidak berubah, untuk LV di node lain berisi hasil `connect`, untuk LV di node ini path aslinya.
  - `test_prepare_recreates_the_config_drive_at_the_same_path`: `create_config_drive` dipanggil dengan `instance_dir` yang sama dengan sumber.
  - `test_prepare_failure_cleans_what_it_made`: `connect` volume kedua melempar: tap yang dibuat dihapus dan `disconnect` volume pertama dipanggil, galat tetap dilempar.
  - `test_prepare_twice_is_harmless` (Review Focus 2): panggilan kedua dengan keadaan yang sama (tap ada, `connect` mengembalikan path yang sama) berhasil dan mengembalikan hasil yang sama.
  - `test_finish_binds_every_tap`: `set_tap_binding` dipanggil untuk tiap port dengan MAC dan server id yang benar.
  - `test_rollback_removes_taps_sessions_and_the_instance_dir_and_never_raises`: tiap langkah dipanggil; `disconnect` melempar di satu volume dan langkah lain tetap jalan; balasan `{"status": "ok"}`.
- [ ] **Step 2: Jalankan** `python -m pytest tests/compute_worker/test_live_migration_target.py -q`; gagal.
- [ ] **Step 3: Implementasikan** `live_migration.py` (fungsi di atas) dan tiga handler tipis di `main.py` yang memanggil fungsi itu dengan `_rpc`.
- [ ] **Step 4: Jalankan** `python -m pytest tests/compute_worker -q`; lulus.
- [ ] **Step 5: Commit** `feat(compute-worker): live migration target side (prepare, finish, rollback)`.

### Task 5: Sisi sumber (`run`) dan handler `live_migrate`

**Files:**
- Modify: `services/compute_worker/app/live_migration.py`, `services/compute_worker/app/main.py` (ganti `handle_live_migrate` lama dan kunci `"live_migrate_instance"` dengan `handle_live_migrate` baru di kunci `"live_migrate"`)
- Test: `tests/compute_worker/test_live_migration_source.py`

**Interfaces:**
- Consumes: Task 3 dan 4; `main.conductor_update_server`, `main._notify_action_finish(server_id, result, message, action)`.
- Payload: payload `live_migrate` = payload build plus `_volumes`, `_target_host: str`.
- Produces di `live_migration.py`: `run(args: dict, rpc, *, update_server, notify) -> None` dengan `update_server(server_id, fields)` dan `notify(server_id, result, message, action)` disuntikkan. Urutan: (1) `rpc.call(host_queue("compute", target), "live_migrate_prepare", {**args, "_source_host": COMPUTE_HOST}, timeout=...)`; (2) `dest_xml = rewrite_disk_sources(domain_xml_for_migration(domain), mapping)` dengan `mapping` = path sumber saat ini (`iscsi.device_path(*session)` bila volume remote di sumber dan sesinya ada, selain itu `volume["path"]`) ke `disk_paths`, hanya untuk yang berbeda; (3) `migrate_domain(domain, LIVE_MIGRATION_URI.format(host=migration_host), dest_xml, ...)` dengan nilai `config`; (4) berhasil: hapus tap sumber (`delete_tap` tiap tap domain; baca tap dari `libvirt_driver.interface_taps` **sebelum** migrasi), `rpc.call` `live_migrate_finish`, `volume_manager.disconnect(volume_id, host, rpc)` untuk volume LV yang `host` bukan node ini, hapus `instance_dir` sumber, `update_server(id, {"host": target, "status": "ACTIVE", "error_message": None})`, `notify(id, "success", None, "live_migrate")`; (5) `MigrationError` atau galat lain di langkah 1-3: `rpc.call` `live_migrate_rollback` (galatnya hanya dicatat), `update_server(id, {"status": "ACTIVE", "error_message": None})`, `notify(id, "error", alasan, "live_migrate")`; galat di langkah 4 setelah migrasi sukses tidak memicu rollback (VM sudah di tujuan): dicatat, status tetap diselesaikan ke `ACTIVE` di tujuan, dan `notify(... "error", "migrated, but cleanup failed: ...")`.
- `main.handle_live_migrate(args) -> None`: menjalankan `live_migration.run` di `threading.Thread(daemon=True)` sehingga konsumer RPC tidak terblokir.

- [ ] **Step 1: Tulis tes** (libvirt, `rpc`, `network_manager`, `volume_manager` palsu; `rpc` merekam `(queue, method)` berurutan):
  - `test_success_order`: urutan panggilan persis: `live_migrate_prepare` -> `migrate_domain` -> `delete_tap` sumber -> `live_migrate_finish` -> `disconnect` volume remote -> `update_server(... host=target, status=ACTIVE)` -> `notify("success")`.
  - `test_dest_xml_uses_the_target_paths_only_for_changed_disks`: mapping hanya berisi LV yang pathnya berbeda; RBD tidak ada di mapping.
  - `test_prepare_failure_changes_nothing_on_the_source`: `prepare` melempar: `migrate_domain` tidak dipanggil, `rollback` dipanggil, status kembali `ACTIVE` tanpa `host`, `notify("error", ...)` dengan alasan dari galat.
  - `test_migration_failure_rolls_back_the_target`: `migrate_domain` melempar `MigrationError("timed out after 1800 s")`: `rollback` dipanggil, `delete_tap` sumber tidak dipanggil, `update_server` tanpa kunci `host`.
  - `test_finish_failure_after_a_successful_migration_does_not_roll_back`: `finish` melempar: `rollback` tidak dipanggil, `update_server` memuat `host=target` dan `status=ACTIVE`, `notify("error", pesan berisi "cleanup")`.
  - `test_rollback_failure_is_logged_and_the_status_is_still_restored`.
  - `test_handler_runs_in_a_thread`: `main.handle_live_migrate` kembali sebelum `run` selesai (gunakan `threading.Event`).
- [ ] **Step 2: Jalankan** `python -m pytest tests/compute_worker/test_live_migration_source.py -q`; gagal.
- [ ] **Step 3: Implementasikan** `run` dan `handle_live_migrate`; hapus fungsi lama dan kunci `"live_migrate_instance"` (tidak ada tes yang memakainya).
- [ ] **Step 4: Jalankan** `python -m pytest tests/compute_worker -q`; lulus.
- [ ] **Step 5: Commit** `feat(compute-worker): live migration source side`.

### Task 6: compute-api: rute `live-migrate` dan pemulihan klaim

**Files:**
- Modify: `services/compute/app/routers/server_actions.py` (hapus rute 501 `live_migrate_server`), `services/compute/app/routers/ops.py` (rute baru dan perubahan `POST /internal/servers/{server_id}/actions/finish`)
- Test: `tests/compute/test_live_migrate.py`

**Interfaces:**
- Consumes: `place_and_build`-nya cold migrate dipakai ulang secara bagian: bangun payload lewat fungsi baru `live_migration_payload(db, server, host) -> tuple[str, dict]` di `ops.py` yang memakai `claim_host(db, server.id, server.project_id, _flavor_of(db, server), host=host, avoid=(server.host,))` (fallback `fallback_host` seperti `place_and_build`), mengambil port dan daftar volume (id, `host`, `path`, `backend`, `target_device`; root dengan `target_dev="sda"`) lewat `clients.block_get`, dan mengembalikan `(target, payload)` dengan `_volumes`, `_target_host`.
- Produces: `POST /servers/{ref}/live-migrate` dengan `Annotated[dict, Depends(policy.require("compute:server:live_migrate"))]`, body `MoveIn | None`. Server tetap di `host` lama sampai worker memperbarui `host`. Status `MIGRATING` di-set sinkron. Aksi dicatat `record_action(db, server.id, "live_migrate", current)`.
- Penolakan 409 (teks menyebut sumbernya): server tidak `ACTIVE` (termasuk sudah `MIGRATING`); terkunci (gunakan `require_unlocked_server`); tidak punya host atau `root_volume_id`; ada volume terpasang yang `status != "in-use"` atau `path` kosong; host tujuan sama dengan host server, `disabled`, atau `forced_down`.
- Perubahan `actions/finish`: bila `action == "live_migrate"` dan `result == "error"`, klaim placement dipulihkan ke host server dengan `claim_host(db, server.id, server.project_id, _flavor_of(db, server), host=server.host)`; galat placement dicatat dan tidak menggagalkan jawaban.

- [ ] **Step 1: Tulis tes** (pola fixture `api` dari `tests/compute/test_catalog_failures.py`, `clients.cast_to_worker` dan `block_get` dipalsukan):
  - `test_a_live_migrate_casts_to_the_source_worker_and_marks_the_server_migrating`: 202, isi `{"server_id", "from", "to", "status": "MIGRATING"}`; cast `live_migrate` dengan `_target_host` dan `_volumes` (volume root `target_dev="sda"`); `server.host` masih host lama.
  - `test_each_refusal_is_a_409_with_its_reason` (parametrize): status `SHUTOFF`; `MIGRATING`; terkunci; tanpa root volume; volume terpasang `status="available"`; volume dengan `path=""` (Review Focus 4); host tujuan `disabled`; host tujuan = host server. Tiap kasus: tidak ada cast, status server tidak berubah.
  - `test_a_second_call_while_migrating_is_a_409_and_makes_no_second_claim` (Review Focus 1): panggilan kedua; `claim_host` dipanggil sekali saja.
  - `test_a_member_is_refused_by_the_policy` (403) dan `test_another_projects_server_is_a_404`.
  - `test_no_candidate_host_is_a_409_no_valid_host`.
  - `test_finish_with_error_restores_the_claim_on_the_source_host`: `POST /internal/servers/{id}/actions/finish` dengan `{"result": "error", "action": "live_migrate", ...}` memanggil `claim_host(..., host=<host lama>)`; dengan `result: "success"` tidak memanggil.
- [ ] **Step 2: Jalankan** `python -m pytest tests/compute/test_live_migrate.py -q`; gagal.
- [ ] **Step 3: Implementasikan** rute, `live_migration_payload`, dan perubahan `actions/finish`; hapus rute 501 beserta importnya yang tidak terpakai.
- [ ] **Step 4: Jalankan** `python -m pytest tests/compute tests/shared -q`; lulus (tes lama yang mengharapkan 501 diubah mengikuti perilaku baru).
- [ ] **Step 5: Commit** `feat(compute): POST /servers/{ref}/live-migrate`.

### Task 7: CLI `server live-migrate` dan `host drain|enable`

**Files:**
- Modify: `cli/cloudx_cli/commands/compute.py` (perintah `server live-migrate`)
- Create: `cli/cloudx_cli/commands/host.py`; daftarkan modulnya di tempat `cli/cloudx_cli/main.py` mengimpor modul perintah lain
- Test: `tests/cli/test_live_migrate.py`, `tests/cli/test_host_drain.py`

**Interfaces:**
- Produces: `cloudx server live-migrate SERVER [--host NODE]` -> `POST {compute}/servers/{SERVER}/live-migrate` dengan body `{"host": NODE}` hanya bila `--host` ada.
- Produces di `host.py`: `plan_drain(node: str, servers: list[dict], volumes: list[dict]) -> list[dict]` (murni) mengembalikan per server di `node` `{"server": nama, "id": id, "action": "live-migrate" | "cold-migrate" | "skip", "reason": str}`. `ACTIVE` tanpa volume (root dan terpasang) yang `host == node` dan `backend != "ceph"`: `live-migrate`; `SHUTOFF`: `cold-migrate`; `ACTIVE` dengan LV di `node`: `skip` dengan alasan menyebut nama volume; status lain: `skip` dengan alasan menyebut statusnya.
- `cloudx host enable NODE` -> `PUT {compute}/os-services/NODE` `{"disabled": false}`.
- `cloudx host drain NODE [--dry-run] [--timeout SECONDS (bawaan 2400)]`: (1) `PUT /os-services/NODE` `{"disabled": true, "disabled_reason": "drain"}` (dilewati pada `--dry-run`); (2) `GET /servers?limit=1000` dan `GET /volumes`, filter `server["host"] == NODE`; (3) `plan_drain`; (4) per rencana, satu per satu: `live-migrate` -> `POST .../live-migrate`, `cold-migrate` -> `POST .../migrate`, lalu poll `GET /servers/{id}` tiap 5 detik: selesai bila `status` kembali `ACTIVE` (atau `SHUTOFF` untuk cold) dengan `host != NODE`; gagal bila status `ERROR`, atau kembali `ACTIVE` dengan `host == NODE` (migrasi dibatalkan), atau melewati `--timeout`; (5) cetak tabel (server, hasil, alasan) lewat `_output`; keluar 0 hanya bila tidak ada `skip` dan tidak ada gagal (node tanpa server: pesan "no servers on NODE", keluar 0).

- [ ] **Step 1: Tulis tes** (pola `tests/cli/test_server_resize.py`: `_api` dipalsukan, urutan panggilan direkam; `time.sleep` dipalsukan):
  - `test_live_migrate_posts_the_host_only_when_given`.
  - `test_plan_drain_*` (murni): ACTIVE tanpa LV lokal -> `live-migrate`; ACTIVE dengan LV `host == node` (root atau data) -> `skip` dengan nama volume; RBD (`backend="ceph"`, `host=None`) -> `live-migrate`; SHUTOFF -> `cold-migrate`; PAUSED dan ERROR -> `skip` dengan status; server di node lain tidak muncul.
  - `test_drain_disables_the_node_then_migrates_one_by_one_and_waits`: urutan panggilan `PUT os-services` -> `POST live-migrate` server 1 -> polling sampai selesai -> server 2.
  - `test_drain_reports_a_migration_that_came_back_to_the_same_node_as_failed` dan `test_drain_times_out`.
  - `test_drain_of_an_empty_node_exits_0` dan `test_drain_exits_nonzero_when_something_was_skipped_or_failed` (Review Focus 5).
  - `test_drain_dry_run_prints_the_plan_and_changes_nothing`: tidak ada `PUT` dan `POST`.
  - `test_enable_puts_disabled_false`.
- [ ] **Step 2: Jalankan** `python -m pytest tests/cli/test_live_migrate.py tests/cli/test_host_drain.py -q`; gagal.
- [ ] **Step 3: Implementasikan** perintah dan `plan_drain`. Kode keluar lewat `ctx.exit(code)`.
- [ ] **Step 4: Jalankan** `python -m pytest tests/cli tests/docs -q`; lulus (tes dokumen memeriksa perintah `cloudx ...` di dokumen ada, jadi dokumen yang menyebut perintah ini ditulis di Task 8 setelah perintahnya ada).
- [ ] **Step 5: Commit** `feat(cli): server live-migrate, host drain and host enable`.

### Task 8: Skrip ssh, dokumen, changelog

**Files:**
- Create: `scripts/setup-migration-ssh.sh` (mode 755), `docs/live-migration.md`
- Modify: `docs/feature-comparison.md` (baris live migration di kedua bagian: "Missing" tidak lagi menyebutnya; baris `POST /servers/{ref}/live-migrate` menjadi tersedia dengan batasnya), `docs/storage-backends.md` dan `docs/scenarios.md` (kalimat "tidak didukung"), `docs/user-guide-cli.md`, `etc/cloudx/cloudx.conf.sample` (enam kunci `live_migration_*`, dikomentari), `CHANGELOG.md` (Unreleased, Added)
- Test: `tests/scripts/test_shell_scripts.py` (tes baru untuk skrip)

**Interfaces:**
- `scripts/setup-migration-ssh.sh [--dry-run] NODE...`: untuk tiap NODE (ssh sebagai `ubuntu` dengan sudo, seperti catatan lab) membuat `/root/.ssh/cloudx_migration` (ed25519, tanpa passphrase) bila belum ada; mengumpulkan kunci publik tiap node; memasukkan semuanya ke `/root/.ssh/authorized_keys` setiap node lain (tanpa duplikat); mengisi `/root/.ssh/known_hosts` root tiap node dengan `ssh-keyscan` node lain; menulis blok `Host` di `/root/.ssh/config` yang memakai `IdentityFile /root/.ssh/cloudx_migration` untuk node lain. Idempoten. `--dry-run` hanya mencetak perintah dan tidak memanggil `ssh`. Tanpa argumen: pesan pemakaian dan keluar 2.

- [ ] **Step 1: Tulis tes** di `tests/scripts/test_shell_scripts.py`: `test_setup_migration_ssh_dry_run_prints_the_plan_and_calls_no_ssh` (PATH diberi `ssh` palsu yang gagal bila dipanggil; keluaran menyebut tiap node dan `cloudx_migration`; keluar 0), `test_setup_migration_ssh_without_nodes_prints_usage` (keluar 2), dan `test_live_migration_doc_commands_exist` bila `tests/docs/test_doc_commands.py` belum memeriksanya otomatis (jalankan dulu tesnya).
- [ ] **Step 2: Jalankan** `python -m pytest tests/scripts/test_shell_scripts.py -q -k migration`; gagal.
- [ ] **Step 3: Tulis skrip** dan **dokumen**. `docs/live-migration.md` memuat: apa yang bisa dan tidak bisa dipindah (RBD ya; LV di node lain ya lewat iSCSI; LV di node sumber hanya lewat `live-migrate` biasa dan dilewati oleh `drain`; tanpa block migration), prasyarat (ssh lewat skrip, port 49152-49215 dan 22 antar compute node, akibat keamanannya: node compute saling punya akses root), enam kunci konfigurasi, perintah `server live-migrate`, `host drain`, `host enable`, jeda jaringan satu sampai tiga detik yang diharapkan, apa yang terjadi saat gagal (server tetap `ACTIVE` di node lama, alasan di `os-instance-actions`), dan pemecahan masalah (`journalctl -u cloudx-compute-worker`, ssh manual `ssh -i /root/.ssh/cloudx_migration root@NODE virsh -c qemu:///system list`). Urutan upgrade: semua compute node dulu, lalu compute-api. Perbarui dokumen lain dan changelog seperti di daftar File.
- [ ] **Step 4: Jalankan** `python -m pytest tests -q -p no:cacheprovider`; harapannya hanya dua kegagalan lama yang tidak terkait (`tests/docs/test_doc_commands.py` karena dokumen fase6 yang belum ter-commit, dan `test_cleanup_scripts_dry_run`), selebihnya lulus.
- [ ] **Step 5: Commit** `docs(live-migration): guide, ssh setup script and changelog`.

---

## Self-review

- **Cakupan spec:** API dan kebijakan (Task 1, 6); alur persiapan, XML, migrasi, berhasil, gagal (Task 3-5); pengecualian rekonsiliator dan `MIGRATING` yang tertinggal (Task 2); konfigurasi (Task 3 dan dokumen Task 8); ssh dan jaringan (Task 8); pengosongan (Task 7); placement dikembalikan (Task 6); dokumen (Task 8). Multi-chassis, TLS, block migration, dashboard sengaja tidak ada (non-tujuan).
- **Konsistensi nama:** `live_migrate` (cast ke sumber), `live_migrate_prepare|finish|rollback` (call ke tujuan), `_volumes`, `_target_host`, `_source_host`, `MigrationError`, `rewrite_disk_sources`, `migrate_domain`, `plug_tap`, `set_tap_binding`, `LIVE_MIGRATION_*` dipakai sama di semua task.
- **Keputusan rencana di luar spec:** perintah `cloudx host enable` ditambahkan (spec hanya menyebut operator mengaktifkan lagi, dan CLI belum punya perintah untuk `/os-services`); `drain` mengembalikan kode keluar bukan nol bila ada yang dilewati.
