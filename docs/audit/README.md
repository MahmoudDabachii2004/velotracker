# VeloTracker — Audit Production-Grade (2026-06-20)

Cette archive contient l'analyse complète de VeloTracker réalisée par ton assistant AI,
avec recherche approfondie sur les specs officielles Bluetooth SIG, l'état de l'art en
vision tracking pedals, et les courbes de puissance réelles des trainers commerciaux.

## 📁 Contenu de l'archive

| Fichier | Lignes | Description |
|---------|--------|-------------|
| `VELTRACKER_AUDIT.md` | 512 | **Audit final du code** — 30 bugs identifiés, 15 améliorations, plan d'action en 4 tiers (~38h de travail total) |
| `VeloTracker_BLE_GATT_Specs.md` | 874 | **Spécifications officielles Bluetooth SIG** byte-par-byte pour CSC (0x1816), FTMS (0x1826), CPS (0x1818), DIS (0x180A), Battery (0x180F), + advertising requirements |
| `VeloTracker_Vision_Research.md` | 907 | **État de l'art vision tracking pedals** — circle fitting (Kasa/Taubin/Pratt/Hyperfit), Kalman filter tuning, HSV vs LAB, OSS references (Viscyc, PeloMon, blum.bike), anti-patterns |

**Total : 2 293 lignes d'analyse technique sourcée.**

## 🎯 Verdict global : 7.5/10

Le projet est **solide algorithmiquement** (Taubin fit, Kalman 4D, weighted OLS avec
exponential decay) mais contient **30 bugs** identifiés + **15 améliorations** pour
atteindre le niveau production.

## 🔴 Top 10 bugs critiques

1. FTMS Control Point non implémenté (ERG mode cassé)
2. Pas de Device Information Service (apps voient "Unknown")
3. Pas de Battery Service
4. FTMS Feature bitmask incomplet
5. README prétend Kasa mais code utilise Taubin
6. Glitch rejection trop lax (1 rad/frame ≈ 1725 RPM)
7. Double smoothing EMA + Kalman (redondant)
8. `WHEEL_TO_CRANK_RATIO = 2.0` commenté "50x11" (50/11 = 4.545)
9. `test_filters.py` path Windows hardcodé
10. `ble_diagnostic.py` ignore `POWER_MODEL`

## 📋 Plan d'action en 4 tiers

| Tier | Temps | Contenu |
|------|-------|---------|
| 🥇 Tier 1 | ~2h | 10 quick wins (bugs critiques faciles) |
| 🥈 Tier 2 | ~6h | BLE compliance pro (DIS, Battery, Control Point) |
| 🥉 Tier 3 | ~10h | Algorithmie state-of-the-art (Hyperfit, LAB, fallbacks) |
| 🏆 Tier 4 | ~20h | Production-grade (multi-trainer, calibration, CI/CD, GUI) |
| **Total** | **~38h** | **VeloTracker → niveau Kinetic inRide ($200)** |

## 📚 Sources principales

### Spécifications officielles Bluetooth SIG
- CSCS v1.0 : https://www.bluetooth.com/specifications/specs/cycling-speed-and-cadence-service-1-0/
- CPS v1.1 : https://www.bluetooth.com/specifications/specs/cycling-power-service-1-1/
- FTMS v1.0 : https://www.bluetooth.com/specifications/specs/fitness-machine-service-1-0/
- GATT Specification Supplement : https://btprodspecificationrefs.blob.core.windows.net/gatt-specification-supplement/GATT_Specification_Supplement.pdf
- Assigned Numbers : https://www.bluetooth.com/specifications/assigned-numbers/

### Recherche académique
- Al-Sharadqah & Chernov 2009 — Error Analysis for Circle Fitting Algorithms (arXiv:0907.0421)
- PMC 4788418 — Frame Rate for Optical Motion Tracking
- MDPI Sensors 2022 — Cadence Detection in Road Cycling

### Projects open source
- Viscyc : https://github.com/csachs/viscyc
- PeloMon : https://github.com/ihaque/pelomon
- blum.bike : https://github.com/sciguy14/blumbike
- SmartSpin2k : https://github.com/doudar/SmartSpin2k
- pycycling : https://github.com/zacharyedwardbull/pycycling

### Courbes de puissance commerciales
- Kurt Kinetic (officielle) : https://kurtkinetic.com/
- CycleOps Fluid2 (empirique) : thebikegeek.blogspot.com
- TrainerRoad VirtualPower : https://support.trainerroad.com
- Zwift zPower : https://zwiftinsider.com

## 🚀 Prochaines étapes

1. **Lire l'audit** : `VELTRACKER_AUDIT.md`
2. **Valider le plan d'action** : choisir Tier 1, 2, 3 ou 4
3. **Exécuter** : créer des branches `fix/tier-1`, `feat/tier-2-ble-compliance`, etc.

## 📝 Notes

- L'audit a été réalisé sur le commit `05b6934` (branche `cross-platform`)
- Les rapports sont des références vivantes : à mettre à jour quand le code évolue
- Tous les bugs sont documentés avec file:line pour faciliter le fix
- Le plan d'action est priorisé par impact/effort

---

_Généré le 2026-06-20 par assistant AI (GLM) — recherche et audit cross-checkés contre sources officielles._
