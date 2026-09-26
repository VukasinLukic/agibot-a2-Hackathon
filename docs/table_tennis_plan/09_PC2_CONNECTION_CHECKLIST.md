# 09 PC2 connection checklist (A2 Ultra)

Cilj: bezbedno doći SAMO do PC2 (Jetson Orin AGX, development PC) A2 robota, uz mentora.
Ovaj dokument ne sadrži lozinke, tokene, ključeve ni nepotvrđene IP adrese. Sve adrese su
placeholderi dok ih mentor uživo ne potvrdi. Potvrđene vrednosti upisujemo u lokalnu
beležnicu, NE u Git.

## Šta kaže dokumentacija (za proveru sa mentorom, nije potvrđeno uživo)

Izvori: `Agibot Network Communication Documentation.docx` (sekcije Setup, Ethernet Method,
WiFi Method (A2)), `Agibot A2 Ultra Documentation.docx` (Hardware, Warnings and Notices,
Supervisor), `docs/agibot/head_screen.md`, `docs/agibot/agibot_development_filesystem_locations.md`,
`ROBOT_CONFIG.md`, `08_MENTORSKI_RAZGOVORI.md`.

- A2 ima dva računara: PC1 (x86, motori, mrežna kartica, senzori, ekran) i PC2 (Jetson Orin AGX 64 GB,
  JetPack 6.0, kamere i LiDAR podaci stižu ovde). Razvoj isključivo na PC2.
- POTVRĐENO UŽIVO 2026-09-26: `ssh agi@192.168.2.50` sa laptopa (statički 192.168.2.119/24) vodi
  DIREKTNO na Orin (`agi@ubuntu-orin`, aarch64, `5.15.136-rt-tegra`), tj. na PC2. Jump preko PC1 nije
  potreban za LAN. Detalji u `10_TITANSUDIJA_ROBOT_CONTEXT.md`.
- Interni opseg robota: PC1 i PC2 imaju adrese u istoj internoj podmreži (u docx-u); PC2 prepoznajemo
  po svojoj internoj adresi u izlazu `hostname -I` / `ip -br addr`.
- `whoami` i `hostname` NISU pouzdani za razlikovanje PC1/PC2. Koristiti adresu iz `hostname -I`.
- SSH korisnik u dokumentaciji: `agi` (i za PC1 i za PC2). Port nije naveden (pretpostavka 22,
  potvrditi). Autentikacija: lozinka (docx), a postojeći repo kod koristi SSH ključ za face host.
- Laptop se podešava statičkom IPv4 adresom na Ethernet adapteru u opsegu A2 Ethernet porta,
  maska iz docx slike ("Ethernet Settings for A2"); gateway i DNS prazni. Tačnu adresu laptopa
  i masku potvrditi sa mentorom (slika u docx-u nije tekstualno čitljiva).
- Supervisor na A2 (preko Etherneta) je na portu 8070 po A2 docx-u; `ROBOT_CONFIG.md` primer
  koristi 8080. Neslaganje, potvrditi.
- Kod za projekte na A2: `/agibot/data/home/agi/Desktop/<project_name>` na Orin (PC2).
  Ne dirati `/agibot/software`, `/opt/ros`, `/var/lib/docker`.
- Ekran: `robot_services/screen_manip/` (`show_message`, `show_message_async`), lanac
  `PlayerEmoticon` na rc_module (PC1 x86) + `ResourceService` na Orin (vidi `docs/agibot/head_screen.md`).
  Postojeći kod SSH-uje na PC1 (face host). To je PC1 radnja i NE radimo je bez mentora.
- Govor: Supervisor voice agent (Soniox STT, Azure OpenAI, Soniox/ElevenLabs TTS); audio bridge
  lokalno na PC2, AIMA EM zaustavlja `agent` i `hal_audio` (`docs/audio_bridge.md`,
  `docs/agibot/aima_em_service_manager.md`).
- Gestovi: `robot_services/gestures/` + katalozi u `robot_services/gestures/catalogs/`
  (`docs/gesture_bridge.md`); A2 ima 133 animacije, 20 verifikovano bezbednih u Supervisoru.
- Kamera: 3 grudne (2 fisheye + centralna RGB), vrat, karlica; RealSense (`hal_d415`).
  `ROBOT_CONFIG.md` primer: `default_device: "/dev/video10"` (nepotvrđeno). Repo: `robot_services/vision/`,
  `camera_testing/`, `camera_share/`, `docs/table_tennis_plan/01_COMP_VISION.md`.
- Status servisa: `aima em doctor` (read-only pregled). `stop-app`/`start-app`/`reset-app` su izmene.
- Logovi: dokumentacija NE navodi putanju sistemskih/Supervisor logova za A2. Pitati mentora.

## Pre povezivanja LAN kablom

- [ ] Windows: proveriti OpenSSH klijent u PowerShell-u: `ssh -V`
- [ ] Ako `ssh` ne postoji: `Get-WindowsCapability -Online -Name OpenSSH.Client*`
- [ ] Ako State nije `Installed`, pokrenuti JEDNOM u PowerShell-u kao Administrator:
      `Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0`
- [ ] Zabeležiti trenutna podešavanja Ethernet adaptera laptopa (DHCP/statika) da ih vratimo posle.
- [ ] Ne menjati ništa na robotu. Menjamo samo IPv4 našeg laptopa, i to tek kad mentor da adresu i masku.
- [ ] Zatvoriti sve skripte koje šalju komande robotu (Supervisor lokalno, test skripte, `locomotion_test.py`).
- [ ] Postaviti `ROBOT_ENABLE=0` u lokalnom okruženju ako se bilo šta pokreće lokalno.
- [ ] Odrediti JEDNU osobu koja kuca u SSH terminalu; druga osoba vodi tabelu dole.
- [ ] Mentor prisutan, E-stop dostupan i poznat timu.

## Povezivanje samo na PC2

Izvršavamo tek kada mentor usmeno potvrdi: `<PC2_IP>`, `<user>`, `<port>`, način auth,
i da li je potreban jump host `<JUMP_HOST_IP>` (PC1) ili direktna konekcija.

Direktno (ako mentor kaže da je PC2 dostupan direktno):

```
ssh <user>@<PC2_IP>
```

Preko jump hosta (samo ako mentor eksplicitno odobri; na PC1 ne otvaramo shell i ne kucamo ništa):

```
ssh -J <user>@<JUMP_HOST_IP> <user>@<PC2_IP>
```

Odmah posle login-a, samo read-only komande, ovim redom:

```
hostname
whoami
ip addr
pwd
ls
```

Dodatno, pouzdana provera da smo na PC2 (read-only):

```
hostname -I
```

Ako `hostname -I` ne sadrži mentor-potvrđenu PC2 internu adresu, ili sadrži adresu koju je mentor
označio kao PC1: odmah `exit`, ništa drugo ne kucati, obavestiti mentora.

Ostalo što se sme pokrenuti tek uz izričitu dozvolu mentora (read-only, ali traži potvrdu):
`aima em doctor`, `nvidia-smi` ili `tegrastats` (kratko, Ctrl+C), `df -h ~`, `ls /dev/video*`.

## NIKADA prema PC1

- Ne otvaramo interaktivni shell na PC1 (jump host sme samo da prosledi konekciju, ako mentor odobri).
- Ne pokrećemo nijednu komandu na PC1, uključujući "samo čitanje".
- Ne menjamo mrežna podešavanja, IP, rute, Wi-Fi, firewall, SSH config ni `authorized_keys`.
- Ne kopiramo fajlove na PC1 (`scp`, `rsync`, ffmpeg render za ekran) i ne instaliramo ništa.
- Ne šaljemo HTTP-RPC pozive na PC1 servise (motori, `McMotionService`, `rc_module`, `PlayerEmoticon`).
- Ne restartujemo, gasimo ni `sudo`-ujemo ništa.
- Ne pokrećemo navigaciju, pokrete, gestove, locomotion, velocity komande ni `aima em stop-app/start-app/reset-app`.
- Ne diramo E-stop, LiDAR, kamere (ne gasimo, ne pokrivamo).

Ista zabrana važi i za PC2 za sve što nije read-only, dok mentor ne kaže drugačije.

## Pitanja za mentore

1. Tačna PC2 IP adresa (i interna i spolja vidljiva) i da li je potreban jump preko PC1?
2. Koja statička IPv4 adresa i maska za naš laptop na LAN kablu? Koji port na robotu?
3. SSH korisnik i port za PC2 (i za jump, ako postoji)?
4. Autentikacija: lozinka ili ključ? Kako je dobijamo privatnim kanalom (ne u Git, ne u chat)?
5. Kako sigurno razlikujemo PC1 od PC2 na ovoj jedinici (koju adresu tražimo u `hostname -I`)?
6. Kamera: koji topic/API/device (`/dev/video10`?), koja kamera gleda sto, rezolucija, stvarni FPS, latencija?
7. Supervisor URL i port (docx kaže 8070, repo primer 8080)? Da li je već pokrenut i ko ga poseduje?
8. Gde su logovi: Supervisor, AimDK/EM, kamera, audio?
9. Postojeći API za ekran (da li smemo koristiti `screen_manip` put koji ide preko PC1?), govor (TTS bez LLM-a) i gestove (koji su bezbedni, point left/right)?
10. Koje komande smemo sami da pokrećemo kada mentor ode, a koje nikad?

## Tabela tokom sastanka

| # | Potvrđena komanda | Rezultat (bez tajni) | Mentor | Vreme |
|---|---|---|---|---|
| 1 |  |  |  |  |
| 2 |  |  |  |  |
| 3 |  |  |  |  |
| 4 |  |  |  |  |
| 5 |  |  |  |  |
| 6 |  |  |  |  |
| 7 |  |  |  |  |
| 8 |  |  |  |  |

## Plan za prvih 10 minuta

| Minut | Radnja | Ko |
|---|---|---|
| 0-1 | Predstavljanje cilja (tekst iz `08_MENTORSKI_RAZGOVORI.md`), pitati ko pokriva mrežu | vođa |
| 1-3 | Pitanja 1-5: PC2 adresa, jump da/ne, laptop IP/maska, user, port, auth | backend |
| 3-4 | Mentor pokazuje port na robotu; povezujemo kabl; postavljamo statički IPv4 na laptopu | operater |
| 4-5 | `ssh -V` i ping samo prema adresi koju je mentor dao | operater |
| 5-7 | Prva SSH konekcija (direktno ili odobren jump), odmah `hostname`, `whoami`, `ip addr`, `hostname -I`, `pwd`, `ls` | operater |
| 7-8 | Mentor potvrđuje da je izlaz PC2; upis u tabelu | zapisničar |
| 8-10 | Pitanja 6-9 (kamera, Supervisor, logovi, ekran/glas/gest API); dogovor šta sledeće demonstrira mentor | vision + persone |

Ako do 5. minuta nema veze: mentor pokazuje svoju radnu konekciju, mi zapisujemo tačnu komandu
(bez tajni) i idemo dalje po `08_MENTORSKI_RAZGOVORI.md`.

## Posle sastanka

- Vratiti Ethernet adapter laptopa na prethodna podešavanja.
- Lozinke/ključeve čuvati van repozitorija. Ne commitovati ovu tabelu ako sadrži adrese.
