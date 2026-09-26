# 02 Vision u realnom vremenu na levom fisheye-u i logika poena

## 1. Koliko brzo će raditi

### Šta je izmereno ovde

BallNet put (`candidates` → `ballnet` numpy → `mht`) pušten je na veštačkim kadrovima (zamućena
pozadina, lagani pomak kamere, 6–20 svetlih pokretnih mrlja). Mreža ima pravi oblik
(3 conv + 512→32→1) sa nasumičnim težinama; brzina ne zavisi od vrednosti težina. Mašina je
Intel Xeon 2,8 GHz, 4 jezgra.

| Ulaz | ROI stola | Kandidata | Ukupno p50 / p95 | Najveći deo |
|---|---|---|---|---|
| 1920×1080 | ne | ~6 | 14 / 20 ms | kandidati (LK tok + warp) 12 ms |
| 1920×1080 | da | ~5 | 9–10 / 13 ms | kandidati 7 ms, mreža 2 ms |
| 1920×1080 | da, bez kompenzacije pokreta | ~5 | 7 / 10 ms | kandidati 4 ms |
| 1920×1080 | da | ~20 | 16 / 22 ms | mreža 5,5 ms |
| 960×540 | da | ~4 | 7,5 / 9 ms | kandidati 6 ms |

Oko pipeline-a (isto merenje):

| Korak | Cena na 1920×1080 |
|---|---|
| `Ros2VideoCapture.read()` kopija + `A2FisheyeCapture` kopija u `bytes` + poređenje sa prethodnim | **~7 ms po pozivu**, i ponavlja se u praznom hodu (vidi 01, greška 2) |
| `cv2.resize` 1920→960 `INTER_AREA` | 0,45 ms (tačan faktor 2) |
| `cv2.resize` 1280→960 `INTER_AREA` | 2,2 ms (faktor nije ceo broj; `INTER_LINEAR` 0,46 ms) |

Tracker (MHT) je zanemarljiv dok je kandidata malo. Međutim, on je čist Python i ima složenost
O(tragova × kandidata): pri 128 kandidata (npr. publika u pozadini, treperenje svetla) može da
skoči na desetine ms. ROI stola zato nije opcija nego obaveza.

### Procena za Orin (PC2)

CPU jezgro Cortex-A78AE na 2,2 GHz je za numpy/OpenCV grubo 1,5–2,5 puta sporije od ovog Xeona,
a na PC2 u isto vreme rade Supervisor, YOLO detektor osoba, LiveKit i audio.

| Scenario | Procena po kadru | Realno fps |
|---|---|---|
| Kao danas (1080p, capture sa kopijama, kompenzacija uključena) | 40–70 ms | **15–25 fps**, uz jedno jezgro stalno zauzeto |
| Sa ispravkama ispod (brojač umesto kopija, ROI u punoj rezoluciji, bez kompenzacije dok robot stoji) | 20–35 ms | **25–30 fps**, dovoljno ako kamera daje 30 |

To je procena, a ne merenje. Prvi posao na robotu je da se izmeri (dokument 05, faza 2):
`ros2 topic hz` na fisheye topicu, rezolucija, i `pipeline.last_ms` po koraku na snimku sa
robota.

### Zašto je za suđenje bitnije kašnjenje nego fps

- Odluka o poenu ne mora da bude u 5 ms. Razmena se završava, pa sledi 1–3 s pauze. Budžet
  „od kraja razmene do predloga u aplikaciji" od oko 0,5–0,8 s je sasvim dobar.
- Ono što mora da bude brzo i ravnomerno jeste **svaki kadar**, bez preskakanja. Brza loptica (do
  ~10 m/s) na 30 fps pređe oko 30 cm između kadrova. Na 15 fps se odskok lako „preskoči", jer
  `RallyEventDetector` traži tri uzastopna posmatranja oko obrta brzine.

### Konkretne preporuke za brzinu (po redosledu koristi)

1. **Capture bez kopija i bez praznog hoda** (01, greška 2): kadar se predaje iz ROS callback-a preko
   reda sa jednim mestom (uvek najnoviji), sa brojačem i `header.stamp`.
2. **ROI u punoj rezoluciji umesto smanjivanja celog kadra.** Iseći pravougaonik oko stola plus
   pojas iznad njega. Loptica ostaje 2× veća u pikselima, a broj piksela je sličan kao kod 960×540.
   Sve pragove (`min_area`, `patch_min_side`, parametre trackera) skalirati prema stvarnoj
   veličini loptice na fisheye-u, a ne prema širini kadra.
3. **Kompenzacija pokreta samo kad robot nije mirno.** Dok je robot ARM-ovan i stoji, grudi se
   pomeraju samo kroz balans. Izmeriti rezidualni pomak; ako je ispod piksela, isključiti
   `compensate_motion` i uštedeti oko 3 ms (40 %). Uključiti je posle gesta, dok ruka mrda
   telo.
4. **Radnu širinu birati kao ceo delilac ulaza** (1920→960, 1280→640), ili `INTER_LINEAR`.
5. **ONNX mreža kroz OpenCV DNN** (već postoji `OnnxBallNet`); na x86 je oko 5× brža od numpy-ja.
   GPU (CUDA/TensorRT) nije potreban za ovako malu mrežu, a on bi uvukao rizik oko JetPack-a, što
   `01_COMP_VISION.md` izričito zabranjuje menjati.
6. **Poseban proces sa `nice`/fiksnim jezgrom** (`taskset`), da Supervisor i vision ne dele isto
   jezgro. Numpy/OpenCV niti ograničiti (`cv2.setNumThreads(2)`, `OMP_NUM_THREADS=2`).
7. Snapshot preko SSE umesto `GET` po kadru: nije hitno, `GET` je izmeren na oko 2–3 ms.

### Postavka kamere (odlučuje koliko će biti tačno)

- Robot stoji kao sudija: bočno, u liniji mreže, 1,5–2 m od bočne ivice stola. Tako je duža osa
  stola približno horizontalna u slici, obe polovine su vidljive, mreža je u sredini kadra (gde je
  fisheye najmanje izobličen), a „udarac" je promena smera po x.
- Levi fisheye gleda levo-napred. Proveriti na prvom snimku da ceo sto, sa oba kraja, staje u
  kadar sa bar 8 px margine (`A2FisheyeCapture.table_in_frame`).
- Kalibracija važi samo dok se robot ne pomeri. Posle hoda do stola ili gesta sa velikim pokretom
  trupa treba je ponovo potvrditi (`camera_moved()` gate već postoji).

---

## 2. Logika poena iz slike (predlog)

### Princip

Vision i dalje samo **predlaže** (`point.propose`). Backend odlučuje, a operater potvrđuje sve dok
benchmark ne pokaže bar 95 % preciznosti. Ovo je samo zamena za `RallyJudge._missed_return`, bez
menjanja ugovora: razlozi `missed_return`, `double_bounce`, `out_after_hit`, `service_fault` i
`unknown` već postoje u `contracts`.

### Ulazi po kadru

- `TrackSample` (observed / predicted / missing) iz `BallTracker`-a;
- `RallyEvent` iz `RallyEventDetector`-a: `bounce` sa polovinom (`end_a` / `end_b`) i `hit`;
- iz snapshot-a: `active_rally_id`, `server_id`, `court_end_by_player`, `calibration_id`.

Polovina odskoka se računa iz **tačke odskoka**. To je jedina tačka za koju je homografija ravni
stola tačna, jer je loptica tada na stolu. Položaj loptice u vazduhu se koristi samo za smer
kretanja („ide ka end_a" ili „ka end_b"), a nikad za „na kojoj je polovini".

### Automat stanja jedne razmene

```
            rally.armed (backend)
                   │
                   ▼
   ┌──────────── SERVIS ────────────┐
   │ čeka se odskok na strani        │
   │ servera, pa na strani primaoca  │
   └───────┬─────────────────────────┘
           │ 1. odskok na strani servera, pa 2. odskok na strani primaoca
           ▼
   ┌──────────── U IGRI ─────────────┐
   │ "napadač" = igrač koji je       │◄───┐
   │ poslednji udario; lopta mora    │    │ odskok na protivničkoj polovini,
   │ jednom da odskoči na            │    │ pa udarac (smer se okrenuo)
   │ protivničkoj polovini           │────┘ → napadač se menja
   └───────┬─────────────────────────┘
           │ jedan od završetaka ispod
           ▼
        PREDLOG (najviše jedan po rally_id)
```

Završeci i pobednik, gde je napadač X, a protivnik Y:

| Šta slika vidi | Razlog | Poen dobija |
|---|---|---|
| Servis: prvi odskok je na strani primaoca, ili loptica ne pređe mrežu | `service_fault` | primalac |
| Posle udarca X lopta **nije** odskočila na polovini Y, a nestala je dalje od kraja stola ili u pravcu poda | `out_after_hit` | Y |
| Posle udarca X lopta je odskočila na **svojoj** polovini (mreža) i nije prešla | `out_after_hit` | Y |
| Lopta je odskočila na polovini Y, pa **drugi put** na polovini Y bez udarca | `double_bounce` | X |
| Lopta je odskočila na polovini Y i nestala je iza kraja Y duže od praga, bez udarca | `missed_return` | X |
| Bilo šta drugo (zaklon, udarac nije jasan, lopta se vratila posle „nestanka", servis let) | nema predloga, ili `unknown` sa niskim poverenjem | operater |

### Pragovi protiv lažnih predloga

- **Nestanak mora da traje:** `missing` neprekidno bar ~400–600 ms (12–18 kadrova na 30 fps),
  a ne jedan kadar. Ako se loptica vrati u tom prozoru, ništa se ne predlaže.
- **Nestanak mora biti „na pravom mestu":** poslednji observed položaj je iza kraja stola, ispod
  visine stola ili izvan ROI-ja u smeru kretanja. Nestanak iznad sredine stola je zaklon.
- **Bez udarca nema promene napadača.** Udarac se često ne vidi (reket zakloni loptu). Zato kao
  udarac važi i to da se smer lopte po dužini stola okrenuo, uz loptu viđenu na obe strane obrta.
- **Poverenje** je sada `min(prob)` posmatranih uzoraka (urađeno). Može se dopuniti time koliko je
  dokaza viđeno (odskoci sa polovinom, trajanje nestanka).
- **Jedan predlog po `rally_id`** ostaje, ali se šalje tek na kraju. Ako je rally već zatvoren
  (operater dodelio poen), predlog se ne šalje.

### Kako proveriti da logika valja (pre robota)

1. Na postojećem snimku (`IMG_5844.MOV`) i na prvim snimcima sa robota ručno označiti 50+ razmena:
   pobednik, razlog, vreme kraja.
2. `benchmark.evaluate` (već postoji) meri preciznost, pokrivenost i uzdržavanje. Pragove iz
   liste iznad birati na tuning delu, a meriti na odvojenom delu.
3. Test fixture za svaki red tabele (sintetički `TrackSample` + `RallyEvent` nizovi), po uzoru na
   `vision/fixtures/missed_return.json`.

### Automatski start razmene

Danas operater mora pre svakog poena da pritisne „Servis" (`rally.arm`, samo operator). Za demo je
bolje da backend sam emituje `rally.armed` posle `point.confirmed` kad je režim `assisted`, a kamera
i robot su spremni. Tada operater po poenu ima samo jedan dodir („Potvrdi"). Ovo je izmena u
`core/engine.py`, na nivou pravila, i pripada osobi 2. Vision ne treba da dobije pravo na
`rally.arm`.

### Zvuk

`sound/` ima dobru logiku (reket, pa dva odskoka na istoj polovini, pa tišina), ali na robotu nema
živog ulaza: mikrofon drži `audio-bridge`, a posle AEC-a udarac se gubi (`sound/README.md`). Za
demo zvuk isključiti (`sound=None`). Ako ostane uključen, `RallyJudge` čeka zaključak zvuka
(`require_sound`) i bez njega ne šalje ništa, pa bi pokrivenost pala na nulu. `live.py` danas ne
prosleđuje zvuk, pa je to u redu.
