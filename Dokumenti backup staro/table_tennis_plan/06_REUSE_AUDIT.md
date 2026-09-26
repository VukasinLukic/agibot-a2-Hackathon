# Audit postojećeg koda za A2 sudiju za stoni tenis

Datum: 26. septembar 2026. Izvor: lokalni checkout `C:\Users\Tea\OneDrive\Dokumenti\a2-hackathon`, HEAD `519ce20` u trenutku pregleda. Pregled je urađen čitanjem koda i testova, bez pokretanja robota, mrežnih poziva robotu, instaliranja paketa ili izvršavanja runtime skripti. Testovi navedeni u dokumentu nisu pokrenuti tokom audita.

Putanje u tabelama su relativne u odnosu na koren repozitorijuma; brojevi iza `:` označavaju proverene početne linije u pregledanom checkoutu. Direktni linkovi vode do fajlova. Novi direktorijumi `table_tennis/*` i frontend feature `table-tennis` predstavljaju predlog implementacije, ne tvrdnju da taj kod već postoji.

## Zaključak za tim

Najviše možemo da iskoristimo od A2 gestova, prikaza kratkog teksta, LiveKit govora, React Supervisora i navigacionih misija. Pravila stonog tenisa, pouzdano prepoznavanje poena, zajednički događaji i korekcije rezultata moraju da se naprave. Postojeći YOLO detektor prati ljude; nije implementiran automatski sudija za stoni tenis.

| Član / grana | Ponovo upotrebiti | Napraviti ili prilagoditi | Procena napora adaptacije |
|---|---|---|---|
| 1 / `comp-vision` | ROS2/H264 ulaz kao opciju, lifecycle servisa, obrasce obrade kadrova | Izvor snimka/USB kamere, timestamp/sequence, kalibraciju stola, ball tracker, predlog poena | Visok za automatsko odlučivanje; nizak za simulator događaja |
| 2 / `backend` | FastAPI/Pydantic obrasce, registraciju rutera, health/lifecycle | `RefereeEngine`, storage, verzionisane ugovore, idempotentne komande, SSE meča, undo | Srednji; gotovo cela domen-logika je nova |
| 3 / `navigation` | A2 mapu/waypoint klijenta, MissionRunner kao referencu, motion katalog i screen renderer | Adapter sa kontrolisanim stanjima, dolazak na jedan teren, trajni scoreboard, red gestova | Srednji do visok zbog stanja fizičkog robota |
| 4 / `persone` | React/Vite UI, PromptBuilder, LiveKit command put, speech/audio servise | Ekran meča, dve persone, komentare iz potvrđenog rezultata, stale/dedup politiku | Srednji; osnovni govor i web nisu od nule |

Procene su relativne, bez obećanja trajanja na hardveru. `SAFE_ONLY`, uspešan HTTP odgovor i prolazak unit testa nisu dokaz da je pokret bezbedno i fizički izveden u konkretnom prostoru.

## 1. Vizija: šta već radi i gde je granica

| Dokaz u kodu | Namena i reuse | Ograničenje |
|---|---|---|
| [ros2_capture.py](../../robot_services/vision/detection/ros2_capture.py), `Ros2VideoCapture`:180; `_on_compressed_video`:265; `read`:294 | OpenCV-sličan interfejs za A2 ROS2 kameru; odvojeno dekodiranje H264 | `read()` kopira poslednji kadar; nema broja kadra ni izvornog timestamp-a u izlazu. Više čitanja mogu vratiti isti kadar. To nije vremenski niz pogodan za računanje brzine bez adaptera. |
| [h264_decoder.py](../../robot_services/vision/h264_decoder.py), `H264StreamDecoder`:119; `H264FrameReceiver`:354 | GStreamer decoder, dekodiranje na worker niti i brojač odbačenih jedinica | Dokumentacioni komentar navodi oko 22 fps na konkretnom robotu; to nije merenje ovog audita niti garancija današnjih performansi. |
| [detector.py](../../robot_services/vision/detection/detector.py), `PersonDetector`:5; `detect_and_track`:43 | Primer YOLO/ByteTrack integracije, izbora GPU i kompatibilnosti FP16 opcije | Poziva `classes=[person_class_id]`, podrazumevano 0. Nije model treniran/testiran za malu brzu ping-pong lopticu; promena naziva klase nije dovoljan razvoj sudije. |
| [vision_controller.py](../../robot_supervisor_v2/app/services/vision_controller.py), `VisionControllerService`:83 | Obrazac procesa, sopstvenog Python okruženja, logova i environment konfiguracije | Postojeći servis je vezan za prisustvo osobe/razgovor; novi detector ne treba da emituje postojeći `VisionPresenceEvent` kao poen. |
| [convert_to_engine.py](../../robot_services/vision/detection/convert_to_engine.py) | Kratak primer `YOLO(...).export(format="engine")` | Skripta izvršava export pri učitavanju; nije bibliotečki helper. Postojeći `.engine`/`.onnx` fajlovi nisu dokaz odgovarajućeg treninga, kompatibilnosti ili tačnosti. |

Predlog za osobu 1: u novom `table_tennis/vision` definisati izvor kadrova nezavisan od robota. Prvi izvor je lokalni snimak, drugi fiksna USB kamera, ROS2 je opcioni adapter. Svaki uzorak nosi najmanje `frame_seq`, `capture_monotonic_ns` i eksplicitno poreklo vremena, rezoluciju i BGR sliku, u skladu sa [zajedničkim ugovorom](05_SHARED_CONTRACT.md). Za ponovljeni kadar ne emituje se novi uzorak. Sačuvati metrike dropped frames, starost kadra i efektivni fps.

`H264FrameReceiver` ima bounded queue podrazumevane veličine 120 access units. Pri popunjavanju red odbacuje stare jedinice i sam kod upozorava na kvar H264 reference chain-a. Ne prepisivati taj red kao univerzalni „latest-frame” algoritam: komprimovane H264 jedinice i već dekodirani nezavisni kadrovi imaju različita pravila odbacivanja. Za lopticu prvo meriti realnu starost slike, veličinu loptice u pikselima i vidljivost odskoka. Broj fps sam po sebi ne dokazuje da su svi događaji vidljivi.

Ball tracker, homografija stola, detekcija odskoka/net/izlaska i klasifikacija završetka razmene ostaju novi posao. Predlog poena treba da sadrži stabilan `winner_id`, rally identitet, confidence i referencu na kalibraciju/raspored strana. Kamera može interno koristiti `left/right`, ali pre slanja mora koristiti mapiranje igrača iz trenutnog stanja backend-a. Gubitak loptice znači neizvesnost, ne automatski poen protiv poslednjeg viđenog igrača.

Ne uključivati `face_store.py`, `face_embedder.py` i `face_identity_flow.py` u MVP: imena i šaljivi poslovni rang mogu da se unesu u aplikaciji. Time se izbegava nepotreban posao sa biometrijom i dodatnim modelima. Lokalni snimci služe za merenje samo uz dogovor učesnika; ne dodavati nefiltrirane snimke/transkripte u Git.

## 2. Backend: infrastruktura postoji, sudijska pravila ne

| Dokaz u kodu | Šta uzeti | Šta ne pretpostaviti |
|---|---|---|
| [main.py](../../robot_supervisor_v2/app/api/main.py):596, `FastAPI`; :612, `app.include_router(...)` | Postojeću aplikaciju i jednu malu tačku uključivanja table-tennis rutera | `main.py` je velik zajednički fajl; četiri osobe ne treba istovremeno da ga preuređuju. |
| [conversation.py](../../robot_supervisor_v2/app/models/conversation.py):35, `AgentCommandStep`; :44, `AgentCommandRequest` | Pydantic stil i transport ka agentu | Ovi modeli nemaju `match_id`, event id, score revision, rally ili rezultat. Ne koristiti ih kao authoritative match schema. |
| [base.py](../../robot_supervisor_v2/app/services/base.py):79, `BaseService`; [registry.py](../../robot_supervisor_v2/app/services/registry.py):30, `ServiceRegistry` | Start/stop/health obrasce za opcione procese | Nema potrebe da čist engine postane obavezna zavisnost robota ili uvozi GPU/LiveKit/ROS2. |
| [main.py](../../robot_supervisor_v2/app/api/main.py):4544, `event_stream` | Primer `StreamingResponse` za SSE | Ovo je snapshot statusa Supervisora na svaku sekundu, uz TODO za event-driven pristup. Nema pouzdani dnevnik događaja meča, replay cursor ni commit-before-publish garanciju. |
| [nav_missions.py](../../robot_supervisor_v2/app/api/nav_missions.py):747, `MissionStore` | Primer čuvanja lokalnog JSON sadržaja | Skladište misija nije transakcioni log rezultata. |
| [transcript_store.py](../../robot_supervisor_v2/app/transcript_store.py):11, `TranscriptStore` | Primer memorijskog snapshot-a | Ograničeno memorijsko skladište razgovora nije baza rezultata niti trajni event store. |

Novo `table_tennis/core` treba da bude čist i deterministički kod. `table_tennis/contracts` definiše jedine zajedničke modele; iz OpenAPI se generišu TS tipovi. U pregledanom frontend `package.json` nema podešenog OpenAPI generatora, pa je to novi scaffold korak. Predloženi API i tipovi iz drugih planova su specifikacija koju tek treba sprovesti.

Backend serijalizuje komande za jedan meč, proverava idempotency key/command id i očekivanu reviziju, pa u jednoj transakciji beleži promenu i događaj. Tek potvrđen događaj sme ka ekranu/govoru/gestu. Kod undo operacije nova revizija poništava raniji poen; revizija se ne vraća unazad. Igrač ima stabilan ID, dok se njegova fizička strana može promeniti.

Ne prenositi rezultat kroz slobodan tekst `AgentCommandRequest.text`, prompt, vision presence ili UI stanje. Nemamo dokaz da postoje pravila 11/razlika 2, servis posle 10:10, izbor prvog servera, promena strana, finalni gem, pauza i undo; ta pravila traže nove domenske testove. Postojeće quiz/survey tokove koristiti eventualno kao inspiraciju za state machine, ne kao engine.

## 3. A2: gestovi, ekran i poziv do terena

### Gestovi — visok stepen ponovne upotrebe

[humanoid_platform/gestures.py](../../humanoid_platform/gestures.py):20 već mapira A2 na `AGIBOT_A2_MOTION_PLAYER`. [agibot_a2_ultra.py](../../robot_services/gestures/catalogs/agibot_a2_ultra.py):6 ima `AGIBOT_A2_ULTRA_CATALOG` sa 20 semantičkih naziva; za demo su neposredno relevantni `wave`, `handshake`, `point left`, `point right`, `thumbs up` i `nod thanks`.

[motion_player.py](../../robot_services/gestures/motion_player.py):30 implementira `AgibotMotionPlayerController`, :45 `execute_gesture`, :119 `_send_motion_command`, :136 `_ensure_motion_table`. Preseti se rešavaju prema `display_name_en` iz živog A2 resource kataloga; numerički ID 13 iz našeg kataloga nije vendor motion ID. Neprepoznat preset se preskače i vraća razlog. Podrazumevani cap je 6 sekundi, uz tri eksplicitna izuzetka za duga objašnjenja. Duga objašnjenja nisu potrebna posle svakog poena.

[gesture_api.py](../../robot_services/gestures/gesture_api.py):45 ima red od 16 stringova; `_execute_gesture`:174 validira/puni red, `_gesture_worker`:229 šalje komande. Odgovor `accepted` znači da je zahtev u redu, ne da je ruka završila pokret. `execute_gesture` vraća HTTP/RPC rezultat, ne univerzalnu potvrdu fizičkog završetka. `force_gesture` može zaobići proveru safety pool-a; table-tennis adapter ga ne treba izlagati igračima ili LLM-u.

Novi `table_tennis/robot` adapter treba da vezuje gest za potvrđeni event ID, trenutnu reviziju i eksplicitno mapiranje `player_id -> robot_left/right`. Kamera-levo, levo na ekranu i leva ruka robota nisu nužno ista strana. Posle undo/switch-sides odbaciti zastarele zahteve koji još nisu poslati. Za MVP samo jedan vlasnik kontroliše gestove; bez duplog slanja istog pokreta i iz glasa i iz robot subscribera. Kataloška oznaka `SAFE_ONLY` zahteva probu dometa pokreta pored stola; ne predstavlja kontaktno/force-controlled rukovanje.

### Ekran — renderer postoji, stalni scoreboard ne

[add_custom_message.py](../../robot_services/screen_manip/add_custom_message.py):95 `show_message`, :118 `show_message_async`, :144 `provision_screen` daju javni ulaz. [emoticon_screen.py](../../robot_services/screen_manip/emoticon_screen.py):97 `AgibotEmoticonScreenController`, :115 `flash_text` renderuju 800×480 MP4 klip, puštaju emoticon slot i vraćaju podrazumevano lice. To nije browser niti API koji direktno iscrtava tekst.

Možemo ponovo koristiti sanitizaciju, pripremu slot-a, render, SSH multiplexing i playback. Potrebna je adaptacija za trajni rezultat: tokom meča poslednji potvrđeni rezultat ostaje vidljiv, a podrazumevano lice vraća se pri eksplicitnom završetku prikaza. Nije dovoljno postaviti ogroman `duration_s`: time se dugo drži worker i komplikovan je prekid/korekcija.

Postojeći `_flash_generation` sprečava deo preklapajućih restore operacija, ali ne nosi match revision niti garantuje deduplikaciju score događaja. Uvesti jednog latest-state render workera, atomsku zamenu pripremljenog klipa, proveru revizije pre playback-a i koordinisan restore. Testirati istovremeni skor 5:4, 5:5 i undo: na kraju mora ostati najnovije stanje, ne lice ili stariji skor. Ne praviti thread za svaki poen i ne puniti FIFO svim zastarelim prikazima.

`_SAFE_TEXT_RE` u `emoticon_screen.py`:50 prihvata uglavnom ASCII i uklanja srpska slova č/ć/š/ž/đ; potrebno je testirati transliteraciju ili proširiti podržani skup uz očuvanu bezbednu tekstualnu obradu. Limit je 24 znaka za primarni i 32 za sekundarni red. Imena ne smeju izgurati veliki broj poena. Prvi demo može prikazivati `ANA 5 : 4 MARKO`, zatim odvojeno server/gem.

`HEAD_SCREEN_ENABLED` se proverava u `livekit-client/agent_main.py`:1354, ne u svakom direktnom pozivu screen modula. Novi adapter mora imati sopstveni eksplicitni enabled/dry-run uslov. Postojeći zajednički `robot_enabled()` u [robot_enable.py](../../robot_services/gestures/robot_enable.py):6 podržava `ROBOT_ENABLE=0` / `AUDIO_TARGET=host` za gestove i screen flash. Navigacioni HTTP put ne postaje automatski bezbedan samo zato što su te promenljive postavljene.

### Navigacija — postoje koristan klijent i veliki runner, potrebna je uska integracija

| Dokaz | Reuse | Obavezna adaptacija/provera |
|---|---|---|
| [a2_map.py](../../robot_services/autonomous_navigation/testing_controls/a2_map.py):129 `GridInfo`; :138 `world_to_pixel`; :184 `MapStore` | Tačne transformacije A2 mape i postojeći waypoint-i | Ne računati drugu transformaciju u frontend-u; A2 format koristi specifičan top-left origin. |
| [a2_nav.py](../../robot_services/autonomous_navigation/testing_controls/a2_nav.py):219 `action_state`; :223 `mc_state`; :630 `cancel_task`; :691 `preflight` | Konkretan A2 RPC klijent, action gate i cancel sa stvarnim task_id | Ne koristiti `task_id=0` za otkazivanje; acceptance nije arrival. Ne pozivati CLI `arm --execute` iz player aplikacije. |
| [nav_missions.py](../../robot_supervisor_v2/app/api/nav_missions.py):1414 `MissionRunner`; :1454 `start`; :1495 `cancel` | Jedan server-side runner, postojeći run/cancel status | `start()` automatski poziva `hold.engage()` pre provere lokalizacije i prazne liste koraka. Zahtev „Pozovi sudiju” ne treba direktno proslediti na taj endpoint bez operator preflight-a i pregleda tog lifecycle-a. |
| Isti fajl :1731 `_wait_terminal` | Polling PNC terminalnog stanja | Nema timeout po dizajnu; ne proverava da pročitani terminalni `task_id` pripada našem cilju. Dodati kontrolisani deadline/watchdog i potvrdu odgovarajućeg task ID-a. |
| Isti fajl :346 `NavRelay`; :368 `last_pose`; :515 `hold_open` | ROS2 sidecar i fresh-pose cache sa istekom | `_run`:1681 garantuje `hold_open` za glass survey, ne za svaku običnu misiju. Novi adapter treba da obezbedi kontinuitet pose telemetrije i kada se browser zatvori. |
| Isti fajl :2082 `ActionHold`; :2121 `engage`; :2132 `release` | Postojeće upravljanje walking action režimom | Aktivno menja/održava stanje motora i idle-motion servisa. Pregledati release/error/cancel ponašanje; nije pasivni health check. |
| [NavigationMissionsTab.tsx](../../robot_supervisor_v2/frontend/src/components/tabs/NavigationMissionsTab.tsx):305 | Operator UI za mapu, rutu, waypoint-e i status | Player PWA treba jedno dugme za poznati teren i jasan status, ne ceo editor proizvoljnih misija/motornih režima. |

Novi adapter mapira `table_id` i `named_waypoint_id` na unapred odobrene `map_id` i waypoint, drži single-flight poziv i razlikuje requested/validating/moving/arrived/ready/failed/cancel_requested/cancelled. Na dolasku potvrđuje terminalni status odgovarajuće misije i svežu pozu kod cilja; po potrebi operator potvrđuje spremnost. Robot ostaje stacionaran tokom igre. Ponovljeni HTTP poziv ne sme pokrenuti drugu misiju. Pauza meča ne sme se tumačiti kao garancija da je navigacija fizički zaustavljena; cancellation ima poseban status i proveru.

Specifičan rizik u sadašnjem kodu: posle `hold.engage`, neuspeh provere lokalizacije ili praznih koraka ne radi lokalni `hold.release` u toj grani. To je razlog za mock test pre korišćenja runner-a, ne dokaz da se robot u ovom auditu pomerio. Celokupan navigacioni lifecycle treba obuhvatiti `try/finally` ili adapterom sa jasnim vlasništvom i recovery procedurom.

## 4. Aplikacija, glas i persone

| Postojeći fajl/simbol | Ponovna upotreba | Dodatni posao |
|---|---|---|
| [frontend/package.json](../../robot_supervisor_v2/frontend/package.json) | React 19, TypeScript, Vite, postojeće UI biblioteke | Novi feature `table-tennis` i mock transport; ne uvoditi drugi web stack. |
| [App.tsx](../../robot_supervisor_v2/frontend/src/App.tsx):1; [api/client.ts](../../robot_supervisor_v2/frontend/src/api/client.ts):121 `APIClient` | Shell, API baza i obrazac request-a | Jedan dogovoreni integration hook za tab. Nov frontend client treba da koristi generisane match tipove. |
| [api/sse.ts](../../robot_supervisor_v2/frontend/src/api/sse.ts):7 `SSEConnection` | Primer reconnect-a i subscribe/unsubscribe | Trenutno hardkoduje `SystemStatus`, resetuje EventSource pri grešci i nema match revision resync. Za rezultat napraviti namenski stream sa snapshot-om posle reconnect-a. |
| [prompt_builder.py](../../livekit_config/prompt_builder.py):53 `PromptBuilder`; :105 `build`; :224 `reload` | Postojeća kompozicija base/persona/context/phase i lokalizacija | Novi `regular_referee` i `corporate_referee` sadržaj; promena persone nikada ne menja engine ili zvaničan rezultat. |
| [agent_commands.py](../../robot_supervisor_v2/app/utils/agent_commands.py):18 `build_command_payload`; :70 `send_agent_command` | Gotov LiveKit byte-stream transport za govor | Svaki poziv otvara pa zatvara LiveKit sobu. Meriti latency; eventualni persistent publisher je svesna adaptacija, ne postojeća osobina. |
| [main.py](../../robot_supervisor_v2/app/api/main.py):2577 `/api/conversation/command`; [agent_main.py](../../livekit-client/agent_main.py):712 `_command_received`; :1012 `_execute_command_steps` | Agent ume da primi gotov tekst/gest i izgovori ga | Uspešan publish potvrđuje slanje, ne „govor završen”. Postojeći payload nema event ID/revision, pa novi dispatcher mora pratiti stale/dedup stanje. |
| [audio_bridge.py](../../robot_supervisor_v2/app/services/audio_bridge.py):24 `AudioBridgeService`; [voice_agent.py](../../robot_supervisor_v2/app/services/voice_agent.py):70 `VoiceAgentService` | A2 lokalni audio i voice process lifecycle | Konfiguracija zavisi od robot okruženja; rad na laptopu mora koristiti mock/host režim. |

Za MVP komentar može biti potpuno deterministički: potvrđeni poen ulazi u template, npr. „Poen Ana. Pet prema četiri.” LLM dobija readonly činjenice i dozvoljen stil, a deo sa zvaničnim rezultatom dolazi iz backend snapshot-a. Ne dozvoliti da LLM generiše novi rezultat ili winner ID. Korporativna persona koristi ručno uneti šaljivi rang i favorizuje igrača samo u komentarima. Važnije najave (poen, servis, gem, korekcija) imaju prioritet nad dužim komentarom; ponovni stream/replay ne ponavlja stare viceve i gestove.

Svega jedan audio vlasnik treba da koristi uređaje. [AIMA_EM.md](../agibot/AIMA_EM.md) i [aima_em_service_manager.md](../agibot/aima_em_service_manager.md) opisuju zaustavljanje native `agent`/`hal_audio` za LiveKit. To je operativna promena na robotu i ne sme se sakriti u laptop scaffold ili startup import. Za ovaj audit nijedan takav servis nije menjan.

## 5. Dokumentacija koja se ne poklapa sa pregledanim kodom

1. `ROBOT_PLATFORM_ABSTRACTION.md` i `ROBOT_PLATFORM_MIGRATION.md` na više mesta opisuju A2 gesture backend kao budući/unsupported. Sadašnji `humanoid_platform/gestures.py`, A2 katalog, `motion_player.py` i odgovarajući testovi dokazuju da implementacija postoji. `docs/gesture_bridge.md` već opisuje novi backend, ali u jednom opisu `arm_controller.py` ostaje Unitree fokus. Za A2 pratiti `motion_player.py`.
2. `a2_nav.py`:23–27 čuva stari komentar „NOT YET VERIFIED” za stvarno kretanje. Dalji komentari iz septembra opisuju realne action/cancel probleme; `nav_missions.py` je mnogo razvijeniji od tog uvoda. Korektan zaključak je „klijent i runner postoje; hardverska pouzdanost u našem prostoru nije proverena”, ne ni „navigacija je gotova” ni „nema navigacije”.
3. Uvod `NavigationMissionsTab.tsx` još navodi TF HTTP RPC. `nav_missions.py` navodi da je taj RPC pokvaren na njihovoj verziji i stvarno koristi ROS2 sidecar `/tf`. Prednost ima aktuelni executable put, uz probu na robotu.
4. `docs/prompts_personas.md` i `ROBOT_CONFIG.md` tvrde da su runtime prompt fajlovi ignored. `git ls-files` u ovom checkoutu vraća `prompt_config.yaml` i sva četiri `prompts/{base,personas,contexts,event_parts}.yaml`. Dakle jesu tracked ovde. Ne pretpostaviti da lokalna izmena nestaje iz Git-a; ne prepisivati korisnikov sadržaj i ne menjati ignore politiku bez zasebne namere. Za feature koristiti namenski versioned sadržaj i eksplicitnu kontrolisanu integraciju.
5. Migracioni dokument predlaže uredan zajednički root `requirements.txt`. Pregledani root fajl je veliki ROS/Ubuntu freeze sa lokalnim `file:///agibot/...whl` putanjama, Jetson/TensorRT i sistemskim paketima. To nije prenosiv install za Windows ili čist laptop.
6. `docs/visual_ui.md` ispravno razlikuje generičku listening/thinking/speaking indikaciju od A2 `head_screen`. A2 scoreboard treba na head-screen backend; Unitree LED runtime nije njegova zamena.

## 6. Zavisnosti, prenosivost i poreklo koda

Za simulator/engine napraviti mali namenski skup zavisnosti (čist Python domen; API Pydantic/FastAPI po potrebi) i odvojene dodatke za CV i robot. Ne pokretati `pip install -r requirements.txt` na svim laptopovima samo zato što je fajl u root-u. `robot_supervisor_v2/requirements.txt` je uži, ali i dalje uključuje audio i RAG pakete; ni njega ne mora da koristi svaki član za čiste domenske testove. `robot_services/vision/detection/requirements.txt` uključuje face recognition modele koji nisu neophodni za lopticu.

ROS2, GStreamer, vendor RPC, SSH, Linux audio uređaji i TensorRT engine zahtevaju odgovarajuće okruženje. Postojeći `.engine` tretirati kao target artefakt dok se ne proveri kompatibilnost sa GPU/JetPack/TensorRT verzijom; na laptopu ne treba da blokira import `table_tennis.core`. Windows služi za development/simulaciju, a ne dokazuje da će Linux robot integracija raditi.

U korenu nisu pronađeni `LICENSE`, `COPYING` ili `NOTICE` fajlovi pretragom tih naziva. U teleoperation podprojektima postoje zasebne licence, ali one ne predstavljaju automatski licencu celog repozitorijuma ili svih težina modela. Ovaj audit ne izvodi pravni zaključak. Zabeležiti poreklo svakog dodatnog modela/dataseta i proveriti njegove uslove pre distribuiranja; lokalni kod samo pokazuje zavisnost od `ultralytics`, ne rešava sve uslove upotrebe.

Ne uvoziti `unitree_sdk2py`, `arm_controller.py` Unitree kontroler, XR/inspire teleoperation ili legacy locomotion skripte u A2 meč. Vendor izbor ostaje na granici `humanoid_platform`/robot adaptera. Ne kopirati `.env`, SSH ključeve, runtime konfiguracije, istorije razgovora, face store ili modele u nove plan/template fajlove. Ovaj dokument ne sadrži vrednosti tajni.

## 7. Testovi koje već možemo da zadržimo

Pregledani testovi su dokazi očekivanog ponašanja, ne izveštaj da su upravo prošli. Pre pokretanja smoke/integration skripti proveriti side effects; nisu svi fajlovi nazvani `test_*` izolovani unit testovi.

| Fajl i primer provere | Šta pokriva | Šta još treba dodati |
|---|---|---|
| [test_motion_player.py](../../robot_supervisor_v2/testing_scripts/test_motion_player.py):70 `test_execute_gesture_resolves_and_plays_motion`; :104 `test_robot_disabled_skips_without_network_call`; :146 discovery failure | Mockovan preset resolver, disabled guard, cap trajanja i neuspeh | Match event dedup, obsolete gesture posle undo, fizička strana igrača, završetak pokreta |
| [test_gesture_catalog.py](../../robot_supervisor_v2/testing_scripts/test_gesture_catalog.py):123 A2 policy; [test_humanoid_platform.py](../../robot_supervisor_v2/testing_scripts/test_humanoid_platform.py):98 gesture support; :128 head screen | A2 support map i dozvoljeni semantički nazivi | Ne dokazuju domet ili bezbednost rukovanja pored stola |
| [test_head_screen.py](../../tests/test_head_screen.py):18 `SanitizeTests`; :44 `RenderScriptTests`; :100 `BackendSelectionTests` | Sanitizacija, render script i backend selection | Persist rezultat, dve revizije u letu, undo tokom render-a, cleanup/default face, srpska imena |
| [test_prompt_builder.py](../../robot_supervisor_v2/testing_scripts/test_prompt_builder.py):15; [test_prompt_locale_personas.py](../../tests/test_prompt_locale_personas.py):9 | Prompt imena i izolacija lokalizovanog persona sadržaja | Isti rezultat u obe persone; persona nema engine write capability |
| [test_self_echo_guard.py](../../tests/test_self_echo_guard.py):27; [test_tts_segment_pipeline.py](../../tests/test_tts_segment_pipeline.py):23 | Echo filtering i normalizacija izgovora | Izgovor rezultata npr. 10:10 bez pretvaranja u vreme, prekid komentara novim poenom |

Dodati nove čiste testove za pravila, replay, dupli zahtev, dva istovremena operatora, stale proposal, undo i promenu strana. Dodati adapter testove sa fake robotom: poziv dva puta pokreće samo jednu misiju, timeout otkazuje pravi task ID, stale pose ne znači arrived, zatvaranje browsera ne gasi watchdog, failed preflight oslobađa ownership. Snimci CV testova imaju ručno označene događaje; meriti accuracy predloga i stopu „unknown”, ne samo detekciju loptice u lepim kadrovima.

## 8. Redosled integracije i prioriteti

1. **P0, svi:** zaključati ugovore i fake adaptere. Osoba 2 poseduje `contracts/core/api/storage`, osoba 1 `vision`, osoba 3 `robot`, osoba 4 `persona` i frontend feature. Integrator unapred dodaje minimalne registration hook-ove kako se ne bi sudarali u `main.py` i `App.tsx`.
2. **P0, osoba 2 + 4:** kompletan meč ručnim poenima, jedno authoritative stanje i reconnect snapshot. Kamera/robot/LLM nisu zavisnosti ovog demo testa.
3. **P0, osoba 3:** fake side-effect izlazi, zatim jedan potvrđeni rezultat na pravom ekranu i jedan kratak gest dok robot miruje. Proveriti persist/revision ponašanje pre većeg broja poena.
4. **P1, osoba 4:** stabilan template govor, neutralna i korporativna persona; zatim opciono LLM komentar. Ne slati istu gest komandu kroz dva izlaza.
5. **P1, osoba 3:** odobreni waypoint i uska call/cancel integracija, sa proverom state/task ID/fresh pose. Zadržati operatorom potvrđeno ručno postavljanje kao funkcionalan način rada.
6. **P1, osoba 1:** replay tracker i kandidat poena, operator confirmation. Automatiku uključiti tek posle merljivog testa na sopstvenom stolu i kameri.

Najveći dobitak od postojećeg projekta je gotova interakcija sa A2. Najveći preostali razvoj je pouzdana odluka kome pripada poen i usaglašavanje svih izlaza sa jednom revizijom rezultata. Zato reuse treba da bude kroz male adaptere, sa simulatorom dostupnim svim članovima od početka.
