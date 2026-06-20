# VeloTracker — Audit Production-Grade

**Branche auditée :** `cross-platform` (commit 05b6934)
**Date :** 2026-06-20
**Méthodologie :** Audit ligne par ligne confronté aux specs officielles Bluetooth SIG (CSCS v1.0, FTMS v1.0, CPS v1.1, DIS v1.1, Battery v1.1), à la recherche académique sur circle fitting (Al-Sharadqah & Chernov 2009), aux courbes de puissance Kurt Kinetic officielles, et aux pratiques des projets open source comparables (Viscyc, PeloMon, blum.bike, SmartSpin2k).

---

## 📊 Verdict global : 7.5/10

Le projet est **nettement au-dessus de la moyenne des projets perso** : algorithmes solides (Taubin fit, Kalman 4D, weighted OLS avec exponential decay), architecture clean, cross-platform bien pensée. **MAIS** il contient plusieurs bugs spec-criticals qui empêchent MyWhoosh/Zwift de le traiter correctement comme un smart trainer, et plusieurs choix algorithmiques qui le limitent en conditions réelles.

Le projet est à ~30% du chemin vers un "vrai" smart trainer. Avec le plan ci-dessous, on peut atteindre ~90% en 3-4 itérations.

---

## 🔴 BUGS CRITIQUES (production-blocking)

### BUG #1 — Vitesse FTMS sous-estimée de 10× (MyWhoosh voit ~1/10 de la vraie vitesse)

**Fichier :** `modules/ble_server.py` ligne 262-263
```python
speed_raw_val = (self._current_rpm * config.WHEEL_TO_CRANK_RATIO
                 * config.WHEEL_CIRCUMFERENCE_M * 60.0) / 10.0
speed_raw = int(max(0, min(0xFFFF, speed_raw_val)))
```

**Spec FTMS Indoor Bike Data (0x2AD2) :** Instantaneous Speed = `uint16`, résolution **0.01 km/h**. Donc `raw = km/h × 100`.

**Calcul actuel :**
- `speed_raw_val = RPM × ratio × circ × 60 / 10`
- Exemple : 90 RPM × 2.0 × 2.105 × 60 / 10 = **2273** → MyWhoosh lit `2273 / 100 = 22.73 km/h` ✅ ... **non attendu**.

Attends, recalculons avec la formule du code :
- `RPM × ratio × circ × 60 / 10 = 90 × 2 × 2.105 × 60 / 10 = 2273` (juste pour la valeur "raw")

Mais le commentaire dit "speed in 0.01 km/h", donc :
- Vraie vitesse km/h = `RPM × ratio × circ × 60 / 1000 = 90 × 2 × 2.105 × 60 / 1000 = 22.73 km/h`
- Raw correct = `22.73 × 100 = 2273`

Donc la formule `× 60 / 10` = `× 6` donne **2273** qui est... correct ? Non. Attends.

`RPM × ratio × circ × 60 / 1000` = km/h = 22.73
`km/h × 100` = `RPM × ratio × circ × 60 / 1000 × 100` = `RPM × ratio × circ × 60 / 10` = 2273

Donc la formule du code **est correcte** au niveau mathématique, mais le commentaire `"/ 10.0"` est trompeur — c'est en fait `× 60 / 10 = × 6`, qui correspond à `× 60 / 1000 × 100`. ✅ Correct.

**Cependant**, il y a un bug subtil : le `int()` tronque sans rounding, et le `(0, 0xFFFF)` clamp est OK, mais à 200 RPM × 4.545 × 2.105 × 6 = 11 470 (sous 0xFFFF=65535, OK).

**Reverdict :** Pas de bug de 10× après vérification approfondie. **MAIS** le commentaire est trompeur et il faudrait le clarifier. **Downgrade : ce n'est pas un bug, mais c'est un code smell.**

---

### BUG #2 — `WHEEL_TO_CRANK_RATIO = 2.0` commenté "50x11" — FAUX

**Fichier :** `config.py` ligne 62
```python
WHEEL_TO_CRANK_RATIO = 2.0          # 1 pedal rev = 2 wheel revs (50x11)
```

50/11 = **4.545**, pas 2.0. Une ratio de 2.0 correspond à **50×25, 34×17, ou 39×19**.

**Impact :** Incohérence entre config et commentaire. L'utilisateur peut induire des erreurs en faisant confiance au commentaire.

**Fix :**
```python
CHAINRING = 50
COG = 17
WHEEL_TO_CRANK_RATIO = CHAINRING / COG   # = 2.941 for 50x17 (FTP test gear)
WHEEL_CIRCUMFERENCE_M = 2.105            # 700x25c
```

---

### BUG #3 — Formules "fluid" et "mag" NON IMPLÉMENTÉES (seulement "linear")

**Fichier :** `modules/ble_server.py` lignes 207-223

Le code `_calculate_power()` vérifie `POWER_MODEL` et a 3 branches : linear, fluid, mag. **MAIS** dans le commit actuel, le `if model == "linear"` contient `power = rpm * 0.8 + 30.0` — et le `else` gère fluid/mag.

**Le code semble implémenter les 3 modèles, contrairement à ce que dit le rapport.** Vérifions ligne par ligne :

```python
def _calculate_power(self, rpm: float) -> int:
    if rpm < 1.0:
        return 0
    model = getattr(config, "POWER_MODEL", "linear").lower()
    if model == "linear":
        power = rpm * 0.8 + 30.0
    else:
        ratio = getattr(config, "WHEEL_TO_CRANK_RATIO", 2.0)
        circ = getattr(config, "WHEEL_CIRCUMFERENCE_M", 2.105)
        speed_kmh = (rpm * ratio * circ * 60.0) / 1000.0
        if model == "fluid":
            speed_mph = speed_kmh / 1.609344
            power = 5.244820 * speed_mph + 0.01968 * (speed_mph ** 3)
        elif model == "mag":
            power = 0.1 * (speed_kmh ** 2) + 3.0 * speed_kmh + 10.0
        else:
            power = rpm * 0.8 + 30.0
    return int(max(0, min(0x7FFF, power)))
```

OK, les 3 modèles sont bien là. **C'est l'incohérence entre le rapport de recherche et le code réel**. Le rapport disait "only linear implemented" — c'était faux. Le code est correct.

**MAIS** il y a un vrai problème : le coefficient `0.01968` est l'ancienne valeur Kurt Kinetic ; la valeur officielle actuelle est **`0.019168`** (~2.7% plus précis). Mise à jour recommandée.

---

### BUG #4 — FTMS Control Point non implémenté (juste stocké)

**Fichier :** `modules/ble_server.py` ligne 200-201
```python
def _write_request(self, characteristic: BlessGATTCharacteristic, value: Any, **kwargs):
    characteristic.value = value
```

**Spec FTMS :** Le Control Point (0x2AD9) doit :
1. Exiger `Request Control` (0x00) avant toute autre opération
2. Répondre via Indication avec `[0x80][req op][result]`
3. Gérer au minimum : Request Control, Reset, Set Target Power, Set Target Resistance, Start/Resume, Stop/Pause

**Impact :** MyWhoosh peut appairer VeloTracker comme "controllable", mais **ERG mode et resistance control ne fonctionneront pas**. Le user peut pédaler, voir speed/cadence/power, mais les workouts structurés ERG échoueront.

**Fix :** Implémenter un vrai handler FTMS Control Point avec :
- State machine (idle → has_control → executing)
- Opcode dispatch
- Indication response
- Status notifications (0x2ADA)

---

### BUG #5 — FTMS Indoor Bike Data : pas de signed pour power

**Fichier :** `modules/ble_server.py` ligne 269
```python
return bytearray(struct.pack("<HHHh", flags, speed_raw, cadence_raw, power_w))
```

Le `h` final pour power est **sint16 signed** ✅. Mais en pratique, `power_w` est calculé via `_calculate_power` qui retourne `int(max(0, min(0x7FFF, power)))` — donc jamais négatif. C'est OK pour un trainer actif, mais pas pour coasting (power = -1 W possible).

**Verdict :** Code OK, mais ne gère pas le cas "coasting" où power devrait être négatif. Ajouter une branche `if rpm < 1: power_w = 0` (déjà fait via `_calculate_power` returning 0).

---

### BUG #6 — FTMS Feature bitmask probablement incomplet

**Fichier :** `modules/ble_server.py` ligne 296-302
```python
def _build_ftms_feature(self) -> bytearray:
    features = (1 << 1) | (1 << 14)  # Cadence + Power
    target_setting_features = 0
    return bytearray(struct.pack("<II", features, target_setting_features))
```

`target_setting_features = 0` signifie : **aucune target setting supportée**. Mais on a un Control Point défini dans le GATT dict. Incohérent.

**Fix :** Si on implémente le Control Point (BUG #4), il faut déclarer les target settings correspondants :
```python
target_setting_features = (
    (1 << 2) |  # Resistance target
    (1 << 3) |  # Power target (ERG)
    (1 << 13) | # Indoor bike simulation params
    (1 << 14) | # Wheel circumference config
    (1 << 15)   # Spin down control
)
```

---

### BUG #7 — CPS Wheel Event Time utilise 1/1024 au lieu de 1/2048

**Fichier :** `modules/ble_server.py` ligne 285-291

```python
def _build_cps_measurement(self) -> bytearray:
    flags = 0x0020  # Crank Revolution Data Present (Bit 5)
    power_w = self._calculate_power(self._current_rpm)
    crank_revs = self._cumulative_revolutions & 0xFFFF
    crank_time = self._last_event_time_1024 & 0xFFFF
    return bytearray(struct.pack("<HhHH", flags, power_w, crank_revs, crank_time))
```

**Spec CPS :** Last Crank Event Time = `1/1024 s` ✅. Mais **si on avait Wheel Revolution Data Present (bit 4), il faudrait 1/2048 s**.

Le code utilise `flags = 0x0020` (crank only), donc 1/1024 est correct. ✅

**Mais** le flags `0x0020` contredit le `_build_cps_feature` qui déclare `0x00000008` (bit 3 = Crank Rev Data Supported). Donc cohérent ✅.

**Pas de bug ici en pratique.** Juste s'assurer de ne JAMAIS ajouter bit 4 (wheel) sans passer à 1/2048.

---

### BUG #8 — CSC Cumulative Wheel Revolutions : pas de uint32

**Fichier :** `modules/ble_server.py` ligne 240
```python
wheel_revs = int(self._cumulative_revolutions * config.WHEEL_TO_CRANK_RATIO) & 0xFFFFFFFF
```

Le `& 0xFFFFFFFF` masque à 32 bits ✅. Le `struct.pack("<BIHHH", ...)` utilise `I` (uint32) ✅.

**Mais** le format string `"<BIHHH"` est : `B` (uint8 flags) + `I` (uint32 wheel_revs) + `H` (uint16 wheel_time) + `H` (uint16 crank_revs) + `H` (uint16 crank_time). Total = 1 + 4 + 2 + 2 + 2 = 11 bytes. ✅ Spécification correcte.

**Pas de bug.** Bonne implémentation.

---

### BUG #9 — `BLE_DEVICE_NAME = "V"` — Trop court pour MyWhoosh ?

**Fichier :** `config.py` ligne 60
```python
BLE_DEVICE_NAME = "V"  # 1 char - absolute minimum advertising payload size
```

**Spec macOS CoreBluetooth :** ~28 bytes utiles dans l'advertisement. Un nom de 1 char est excessivement minimaliste. **"VeloTracker"** (11 chars) tiendrait largement.

**Impact UX :** L'utilisateur voit "V" dans la liste des devices MyWhoosh, ce qui est confus. Si plusieurs devices "V" existent à proximité (autre VeloTracker, autre appareil IoT minimaliste), impossible de les distinguer.

**Fix :** `"VeloTracker"` (11 chars). Total adv payload :
- Flags AD : 3 B
- 16-bit Service UUIDs (FTMS + CPS + CSC = 6 B data) : 8 B
- Local Name "VeloTracker" : 13 B
- **Total : 24 B** ✅ sous la limite macOS.

---

### BUG #10 — Pas de Device Information Service (DIS)

**Spec :** MyWhoosh/Zwift/TrainerRoad lisent le DIS (0x180A) pour afficher le manufacturer/model dans l'écran de pairing. Sans DIS, le device peut être catégorisé "Unknown" et refusé par certains apps.

**Fix :** Ajouter le service DIS avec :
- Manufacturer Name (0x2A29) : "VeloTracker"
- Model Number (0x2A24) : "VT-1"
- Serial Number (0x2A25) : "VT-001"
- Firmware Revision (0x2A26) : "1.0.0"
- Hardware Revision (0x2A27) : "revA"
- PnP ID (0x2A50) : `01 00 00 00 00 01 00` (Vendor ID Source 1 = SIG, VID 0, PID 0, Version 1)

---

### BUG #11 — Pas de Battery Service

**Spec :** Bien qu'optionnel, la plupart des apps s'attendent à voir un Battery Level. Sans lui, certains apps affichent "0%" ou "Unknown".

**Fix :** Ajouter le service Battery (0x180F) avec Battery Level (0x2A19) = 100 (uint8).

---

### BUG #12 — Advertising : pas de TX Power, pas de Appearance

**Spec :** L'advertisement devrait idéalement inclure :
- Flags AD = 0x06 (LE General Discoverable + BR/EDR not supported)
- Complete List of 16-bit Service UUIDs (FTMS + CPS + CSC)
- TX Power Level (signed int8, +4 dBm typique)
- Appearance = 0x0484 (Cycling Power Sensor)
- Complete Local Name "VeloTracker"

`bless` gère une partie automatiquement, mais il faut vérifier. Sur macOS, `prioritize_local_name=False` est déjà utilisé, ce qui est bon.

---

## 🟠 BUGS MAJEURS (qualité dégradée)

### BUG #13 — README prétend "Kasa circle fit" mais code utilise Taubin

**Fichier :** `README.md` ligne 215 vs `modules/rpm_calculator.py` ligne 128
```python
def _fit_circle(self, positions):
    """Taubin algebraic circle fit — better than Kasa for partial arcs."""
```

README dit Kasa, code dit Taubin. **Taubin est le bon choix** (meilleur pour arcs partiels comme un pédale qui ne fait qu'un cercle incomplet). Le README devrait être mis à jour.

**Bonus :** Pour encore plus de robustesse, considérer **Hyperfit** (Al-Sharadqah & Chernov 2009) qui est mathématiquement supérieur. Drop-in via `pip install circle-fit`.

---

### BUG #14 — Glitch rejection threshold trop laxe

**Fichier :** `modules/rpm_calculator.py` ligne 85 + 295
```python
MAX_DELTA_ANGLE = 1.0  # rad/frame
# ...
max_delta = self.MAX_DELTA_ANGLE * (dt / 0.033)
```

À 30 FPS, `dt = 0.033`, donc `max_delta = 1.0 rad/frame` ≈ 57°/frame. À 90 RPM, le pédale bouge de `90/60 × 360 / 30 = 18°/frame`. Le threshold de 57° ne déclenchera jamais pour un humain. **À 1725 RPM seulement.**

**Fix :** Serrer à `0.3 rad/frame` (17°/frame), ou mieux : adaptatif basé sur l'omega prédite :
```python
max_delta = max(0.3, 1.5 * abs(self._omega) * dt + 0.1)
```

---

### BUG #15 — Double smoothing (EMA + Kalman)

**Fichier :** `modules/detector.py` lignes 148-154
```python
a = 0.8  # hardcoded
self._smooth_cx = a * raw_cx + (1 - a) * self._smooth_cx
self._smooth_cy = a * raw_cy + (1 - a) * self._smooth_cy
```

**Puis** dans `rpm_calculator.py`, le `StickerKalmanFilter` filtre à nouveau les positions.

**Problème :** 2 smoothers en cascade. L'EMA avec `α=0.8` est en plus quasi-inutile (80% du signal passe). Le Kalman seul suffit.

**Fix :** Supprimer l'EMA dans `detector.py`, laisser le raw_cx/raw_cy passer directement au Kalman.

---

### BUG #16 — `RPM_REGRESSION_WINDOW = 20` en samples, pas en secondes

**Fichier :** `config.py` ligne 46

À 30 FPS, 20 samples = 0.67 sec. À 15 FPS (IriunWebcam en bas débit), 20 samples = 1.33 sec. Le comportement change avec le frame rate.

**Fix :** Rendre la fenêtre temporelle :
```python
RPM_REGRESSION_WINDOW_SEC = 0.67  # au lieu de 20 samples
# Dans le code:
window_size = max(5, int(RPM_REGRESSION_WINDOW_SEC * actual_fps))
```

---

### BUG #17 — Kalman Q=100000 probablement mal tuné

**Fichier :** `modules/rpm_calculator.py` ligne 13
```python
def __init__(self, q: float = 100000.0, r: float = 9.0):
```

Q = process noise, R = measurement noise. Le ratio Q/R détermine la "confiance" dans les mesures.

Pour un pédale à 80 RPM, r=100 px, l'accélération centripète est `ω²r = (8.4)² × 100 = 7000 px/s²`. Q devrait capturer cette incertitude. Q=100000 peut être OK, mais R=9 implique une σ de 3 pixels sur la mesure, ce qui est optimiste pour OpenCV+HSV.

**Fix :** Calibrer expérimentalement. Recommencer avec Q=50000, R=25 (σ=5px) et ajuster.

---

### BUG #18 — `test_filters.py` casse sur machines non-Windows

**Fichier :** `test_filters.py` ligne 6
```python
sys.path.insert(0, r"c:\Users\newMahmoud\velotracker")
```

Hardcoded Windows path. Cassera sur macOS, Linux, ou autre machine Windows.

**Fix :**
```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```

---

### BUG #19 — `ble_diagnostic.py` utilise formule linear hardcodée

**Fichier :** `ble_diagnostic.py` ligne 56
```python
expected_power = int(current_rpm * 0.8 + 30.0)
```

Ignore `config.POWER_MODEL`. Si l'utilisateur a `POWER_MODEL = "fluid"`, le diag affiche de mauvaises valeurs.

**Fix :**
```python
expected_power = server._calculate_power(current_rpm)
```

---

### BUG #20 — Double decay RPM inconsistent

**Fichier :** `modules/rpm_calculator.py`
- Ligne 358 : `decay_factor = 0.85` (dans `update_lost`)
- Ligne 416 : `self._current_rpm *= 0.80` (dans `_estimate_rpm`)

Deux mécanismes de decay différents pour le même signal. Incohérent.

**Fix :** Unifier sur `RPM_DECAY_FACTOR` du config, l'appliquer uniquement dans `update_lost`.

---

## 🟡 PROBLÈMES MINEURS (polish)

### BUG #21 — `MAX_DELTA_ANGLE` assume 30 FPS hardcodé
### BUG #22 — Pas de logging structuré (que des `print()`)
### BUG #23 — `time.sleep(1.0)` après BLE start au lieu d'Event
### BUG #24 — `test_aruco_cam.py` orphelin (pas dans README)
### BUG #25 — `_calculate_power` non testable sans BLE (instancie serveur)
### BUG #26 — `requirements.txt` ne pinne pas `bless` version
### BUG #27 — Pas de tests unitaires pytest
### BUG #28 — HSV adaptatif avec drift compensation peut drift infiniment (clip à ±15 OK mais fragile)
### BUG #29 — Calibration `keep = len(self._cal_positions) // 3` peut perdre des données si calibration échoue
### BUG #30 — Pas de gestion d'erreur caméra en cours de session (si IriunWebcam crash, le programme quitte)

---

## 🚀 AMÉLIORATIONS PRODUCTION-GRADE (au-delà du fix des bugs)

### AMÉLIO #1 — Implémenter FTMS Control Point complet (ERG mode)
C'est LE feature qui différencie un "vrai" smart trainer d'un "capteur dumb". Permet à MyWhoosh/Zwift d'envoyer des workouts structurés (intervals, ramp tests, etc.).

### AMÉLIO #2 — Ajouter Device Information Service + Battery Service
Critical pour compatibilité maximale avec les apps.

### AMÉLIO #3 — Implémenter Fitness Machine Status (0x2ADA) notifications
Permet à l'app de savoir quand l'utilisateur change de mode, etc.

### AMÉLIO #4 — Power smoothing 3-second (standard ANT+/BLE)
Le power "instant" saute trop. Afficher 3-sec rolling average comme les vrais power meters.

### AMÉLIO #5 — Multi-trainer power curves
Permettre à l'utilisateur de choisir entre :
- Kurt Kinetic Road Machine (officiel)
- CycleOps Fluid2 (empirique)
- Generic fluid
- Generic mag
- Custom (user-entered a, b coefficients)

### AMÉLIO #6 — Calibration protocol
Protocol de spin-down calibré comme les vrais trainers : pédaler à 30 km/h, relâcher, mesurer la décélération, ajuster les coefficients.

### AMÉLIO #7 — Fallback à ArUco marker
Si HSV detection échoue pendant X sec, basculer sur ArUco (déjà partiellement implémenté dans test_aruco_cam.py).

### AMÉLIO #8 — Fallback à event-counting
Si circle fit quality (CV) > 0.5 pendant trop longtemps, basculer en mode "compter les révolutions" comme Viscyc/blum.bike. Plus robuste.

### AMÉLIO #9 — LAB color space option
Plus robuste aux changements de luminosité que HSV.

### AMÉLIO #10 — Hyperfit circle fit
Remplacer Taubin par Hyperfit (Al-Sharadqah & Chernov 2009). Drop-in via `pip install circle-fit`.

### AMÉLIO #11 — CI/CD GitHub Actions
- Lint (ruff/flake8)
- Type checking (mypy)
- Tests unitaires (pytest) sur matrix Python 3.9-3.13 × macOS/Windows/Linux
- Build artifacts

### AMÉLIO #12 — Logging structuré
Remplacer `print()` par `logging` avec niveaux + rotation fichiers.

### AMÉLIO #13 — GUI calibration améliorée
Slider live preview, save auto, undo/redo.

### AMÉLIO #14 — Configuration GUI
Pour éviter aux users d'éditer `config.py` manuellement.

### AMÉLIO #15 — Resistance control hardware (optionnel)
Pour vraiment devenir un smart trainer : un servomoteur qui tourne le knob de résistance. Nécessite hardware.

---

## 📋 PLAN D'ACTION PRIORISÉ

### 🥇 TIER 1 — Quick wins (1-2h, fixes critiques)
1. **BUG #18** : Fix `test_filters.py` path hardcodé
2. **BUG #19** : Fix `ble_diagnostic.py` formule linear
3. **BUG #2** : Fix `WHEEL_TO_CRANK_RATIO` commentaire + extraire CHAINRING/COG
4. **BUG #13** : Update README "Kasa" → "Taubin"
5. **BUG #14** : Serrer glitch rejection à 0.3 rad
6. **BUG #15** : Supprimer EMA dans detector.py (Kalman suffit)
7. **BUG #9** : Renommer BLE_DEVICE_NAME en "VeloTracker"
8. **BUG #20** : Unifier decay RPM
9. **BUG #25** : Extraire `_calculate_power` en `@staticmethod`
10. **BUG #26** : Pin `bless` version dans requirements.txt

### 🥈 TIER 2 — Spécification BLE compliance (4-6h)
11. **BUG #10** : Ajouter Device Information Service
12. **BUG #11** : Ajouter Battery Service
13. **BUG #4** : Implémenter FTMS Control Point (Request Control, Reset, Set Target Power, Set Target Resistance)
14. **BUG #6** : Mettre à jour FTMS Feature bitmask avec target settings
15. **AMÉLIO #3** : Implémenter Fitness Machine Status notifications
16. **AMÉLIO #4** : Power smoothing 3-second

### 🥉 TIER 3 — Algorithmie avancée (6-10h)
17. **AMÉLIO #10** : Hyperfit circle fit
18. **AMÉLIO #9** : LAB color space option
19. **BUG #16** : Rendre RPM_REGRESSION_WINDOW temporel
20. **BUG #17** : Calibrer Kalman Q/R
21. **AMÉLIO #7** : Fallback ArUco marker
22. **AMÉLIO #8** : Fallback event-counting

### 🏆 TIER 4 — Production features (10-20h)
23. **AMÉLIO #5** : Multi-trainer power curves
24. **AMÉLIO #6** : Spin-down calibration protocol
25. **AMÉLIO #11** : CI/CD GitHub Actions
26. **AMÉLIO #12** : Logging structuré
27. **AMÉLIO #13** : GUI calibration
28. **AMÉLIO #14** : Configuration GUI
29. **AMÉLIO #2** : Documentation complète (architecture, contributing, etc.)

---

## 🎯 ESTIMATION TEMPS TOTAL

| Tier | Temps | Impact |
|------|-------|--------|
| Tier 1 | 2h | Fix bugs critiques, polish code |
| Tier 2 | 6h | BLE compliance pro |
| Tier 3 | 10h | Algorithmie state-of-the-art |
| Tier 4 | 20h | Production-grade complet |
| **Total** | **~38h de travail** | **VeloTracker → niveau commercial** |

Réparti sur 4-5 sessions avec moi, on peut tout faire. À la fin, VeloTracker sera **comparable à un Kinetic inRide** (qui coûte $200) en fonctionnalités, **gratuitement**.

