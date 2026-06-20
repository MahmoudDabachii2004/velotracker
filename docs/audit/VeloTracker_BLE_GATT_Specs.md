# VeloTracker — Bluetooth GATT Cycling Service Specification Audit

**Purpose:** Authoritative, byte-level reference for the official Bluetooth SIG GATT
specifications relevant to simulating a BLE smart trainer for MyWhoosh / Zwift /
TrainerRoad. Use this to audit the VeloTracker implementation against the specs.

**Sources (all official Bluetooth SIG):**
- GATT Specification Supplement (GSS): https://btprodspecificationrefs.blob.core.windows.net/gatt-specification-supplement/GATT_Specification_Supplement.pdf
- Service spec PDFs/HTML on bluetooth.com (e.g. CSCS, CPS, FTMS v1.0)
- SIG-assigned characteristic XML definitions (machine-readable, the *true* source of byte layouts):
  - https://github.com/oesmith/gatt-xml (mirror of `org.bluetooth.characteristic.*.xml`)
- Assigned Numbers document (UUIDs, appearance values, company IDs):
  https://www.bluetooth.com/specifications/assigned-numbers/

**Conventions used below:**
- All multi-byte numeric fields are **little-endian (LSO first)** unless stated.
- "sint" = signed two's-complement integer; "uint" = unsigned integer.
- 16-bit UUIDs are shorthand; the full 128-bit form is `0000<16bit>-0000-1000-8000-00805f9b34fb`.

---

## ⚠️ Read this first — the spec traps that bite simulator authors

These are the subtle, repeatedly-misimplemented points. Each is detailed in its
section; this list is the executive summary for the audit:

1. **CSC Cumulative Wheel Revolutions is `UINT32` (4 bytes)** — NOT `uint16`. Many
   hobby implementations (notably the PeloMon) ship it as `uint16` and are
   non-compliant. Cumulative Crank Revolutions is `uint16` (2 bytes).
2. **CPS event-time asymmetry:** in Cycling Power Measurement, **Last *Wheel* Event
   Time = 1/2048 s** but **Last *Crank* Event Time = 1/1024 s**. In CSC, *both* are
   1/1024 s. Mixing these up produces 2×/0.5× speed errors.
3. **CPS Instantaneous Power is `sint16` (SIGNED).** Negative power is valid
   (coasting/spindown). Accumulated Torque, however, is `uint16` (unsigned).
4. **FTMS Indoor Bike Data bit 0 ("More Data") is INVERTED:** `0` ⇒ Instantaneous
   Speed *present*; `1` ⇒ Instantaneous Speed *absent*. This is the opposite of every
   other bit in the flags.
5. **FTMS Indoor Bike Data bit 1 vs bit 2:** the SIG's published `indoor_bike_data.xml`
   had the *names* of bits 1 and 2 swapped for years. Authoritative mapping (per
   FTMS spec Table 4.10 and the field transmission order) is:
   **bit 1 = Average Speed present**, **bit 2 = Instantaneous Cadence present**.
6. **Indoor Bike Data Instantaneous Power, Average Power, and Resistance Level are all
   `sint16` (SIGNED).** Resistance is unitless with 0.1 resolution.
7. **Indoor Bike Data Total Distance is `uint24` (3 bytes)** — a 24-bit field.
8. **FTMS Control Point (0x2AD9) requires an ENCRYPTED link** (Table 4.1 security =
   "Encryption"). ⇒ Pairing/bonding is mandatory for any app to control the trainer.
   Control Point is **Write + Indicate** (CCCD must enable *indications*, not notifications).
9. **FTMS requires `Request Control` (0x00) before any control procedure.** Forgetting
   it yields Result Code `0x05 Control Not Permitted`.
10. **FTMS response opcode is `0x80`**, not `0x20` (0x20 is the CPS control-point
    response opcode). Response = `[0x80][Request Op Code][Result Code][opt param]`.
11. **There is NO dedicated "Fitness Machine" / "Indoor Bike" GAP Appearance value.**
    Smart trainers use `Cycling: Power Sensor` = `0x0484` (or `Speed and Cadence
    Sensor` = `0x0485`) and are discovered via the advertised FTMS service UUID.
12. **macOS CoreBluetooth advertising payload** effectively limits you to ~28 usable
    bytes. Use 16-bit service UUIDs (4 B each incl. header), not 128-bit (18 B each).

---

## 1. CSC Service (0x1816) — Cycling Speed and Cadence

**Official spec:** Cycling Speed and Cadence Service v1.0
( https://www.bluetooth.com/specifications/specs/cycling-speed-and-cadence-service-1-0/ )
**GSS sections:** 3.69 CSC Feature, 3.70 CSC Measurement, Sensor Location, SC Control Point.

### 1.1 Service & characteristic UUIDs

| Name | 16-bit UUID | Full 128-bit | Properties |
|------|-------------|--------------|------------|
| Cycling Speed and Cadence Service | `0x1816` | `00001816-0000-1000-8000-00805f9b34fb` | Primary Service |
| CSC Measurement | `0x2A5B` | `…2A5B…` | **Notify** (M) |
| CSC Measurement CCCD | `0x2902` | — | Read, Write (M) |
| CSC Feature | `0x2A5C` | `…2A5C…` | **Read** (M) |
| Sensor Location | `0x2A5D` | `…2A5D…` | Read (C.1) |
| SC Control Point (SCCP) | `0x2A55` | `…2A55…` | **Write + Indicate** (C.2) |
| SCCP CCCD | `0x2902` | — | Read, Write (C.2) |

> ⚠️ **Correction:** the SCCP UUID is **`0x2A55`**, not `0x0055` as sometimes quoted.
> `0x2A55` is shared with the RSC Service control point.

### 1.2 CSC Measurement (0x2A5B) — byte layout

Flags is 1 byte (8-bit). Fields are present conditionally; order is LSO→MSO.

```
Offset  Size   Field                          Type     Unit / Resolution
------  -----  -----------------------------  -------  ----------------------------
0       1      Flags                          uint8    bitmask (see 1.3)
1       4      Cumulative Wheel Revolutions   uint32   revolutions  [if bit0=1]
5       2      Last Wheel Event Time          uint16   1/1024 s     [if bit0=1]
7       2      Cumulative Crank Revolutions   uint16   revolutions  [if bit1=1]
9       2      Last Crank Event Time          uint16   1/1024 s     [if bit1=1]
```

**Canonical example (wheel + crank, no stop):** `[flags][wrev:4][wtime:2][crev:2][ctime:2]` = 11 bytes.
**Crank-only:** `[flags][crev:2][ctime:2]` = 5 bytes (flags = 0x02).

Key rules (from the service spec §3.1):
- **Cumulative Wheel Revolutions is `UINT32`** and is *not permitted to roll over*; it
  may decrement (rolling backwards) but not below 0. Reset only via SCCP Set Cumulative Value.
- **Cumulative Crank Revolutions is `UINT16`** and *is intended to roll over*; not configurable.
- Both event-time fields roll over every 64 s (2^16 / 1024).
- Typical notification interval: **~1 Hz** (spec §3.1.1.4: "approximately once per second").
- Time-sensitive data: if the notification isn't delivered (link loss), the value
  shall be discarded (§3.5).

### 1.3 CSC Measurement Flags (8-bit)

| Bit | Name | 0 | 1 |
|-----|------|---|---|
| 0 | Wheel Revolution Data Present | fields absent | fields present |
| 1 | Crank Revolution Data Present | fields absent | fields present |
| 2–7 | Reserved for Future Use (shall be 0) | — | — |

At least one of bit0/bit1 must be 1 (one or both field pairs must be present).
A feature bit in CSC Feature must be 1 for the corresponding present-bit to ever be 1.

### 1.4 CSC Feature (0x2A5C) — 16-bit bitmask

| Bit | Feature |
|-----|---------|
| 0 | Wheel Revolution Data Supported |
| 1 | Crank Revolution Data Supported |
| 2 | Multiple Sensor Locations Supported |
| 3–15 | RFU |

A smart-trainer-style CSC (cadence only) typically sets bit 1 only ⇒ value `0x0002`.
Speed+cadence ⇒ `0x0003`.

### 1.5 Sensor Location (0x2A5D) — uint8 enum

| Value | Location | | Value | Location |
|------:|----------|-|------:|----------|
| 0 | Other | | 9 | Front Hub |
| 1 | Top of shoe | | 10 | Rear Dropout |
| 2 | In shoe | | 11 | Chainstay |
| 3 | Hip | | 12 | Rear Wheel |
| 4 | Front Wheel | | 13 | Rear Hub |
| 5 | Left Crank | | 14 | Chest |
| 6 | Right Crank | | 15 | Spider |
| 7 | Left Pedal | | 16 | Chain Ring |
| 8 | Right Pedal | | 17–255 | RFU |

> ⚠️ There is **no "top of chain"** value. Smart trainers usually report **`13` Rear Hub**
> or `15` Spider / `16` Chain Ring. `5` Left Crank is common for crank-based power meters.

### 1.6 SC Control Point (SCCP, 0x2A55)

Write (with response) + Indicate. CCCD must enable indications. **Mandatory if** the
Wheel Revolution Data feature OR Multiple Sensor Locations feature is supported.

**Op Codes (uint8):**

| Op | Name | Parameter | Applicable responses |
|----|------|-----------|----------------------|
| `0x00` | Reserved | — | — |
| `0x01` | Set Cumulative Value | Cumulative Value (UINT32 for CSC) | Success, Operation Failed, Op Code Not Supported |
| `0x02` | Start Sensor Calibration | none | *(Not used in CSC v1.0 — defined for RSC)* |
| `0x03` | Update Sensor Location | Sensor Location (uint8) | Success, Invalid Parameter, Op Code Not Supported |
| `0x04` | Request Supported Sensor Locations | none | Success (+ list), Operation Failed, Op Code Not Supported |
| `0x10` | Response Code | (response payload) | — |
| `0x05–0x0F`, `0x11–0xFF` | RFU | | |

**Response Value (uint8):** `0x00` RFU · `0x01` Success · `0x02` Op Code not supported ·
`0x03` Invalid Parameter · `0x04` Operation Failed.

**Response indication format:**
`[0x10][Request Op Code][Response Value][optional Response Parameter]`
For Op Code `0x04` on Success, the Response Parameter is a byte array of supported
sensor-location values (≤17 for default ATT MTU).

**Application error codes (ATT):** `0x80` Procedure Already in Progress,
`0x81` Client Characteristic Configuration Descriptor Improperly Configured.

### 1.7 Common CSC implementation mistakes
- Shipping Cumulative Wheel Revolutions as `uint16` (must be `uint32`). **PeloMon is
  non-compliant here.**
- Computing the "last event time" as the current clock instead of the time the *last
  integral revolution completed* — produces wrong/jittery speed.
- Forgetting the SCCP characteristic entirely. Garmin watches refuse to show CSC
  speed/cadence until a (even no-op) SCCP "Set Cumulative Value" handler exists.
- Not honouring the "shall not roll over" rule for wheel revs, or letting it go below 0.
- Setting RFU flag bits to 1.
- Sending notifications faster than ~1 Hz with no benefit (spec says ~1/s; clients
  compute deltas between notifications).

---

## 2. FTMS Service (0x1826) — Fitness Machine Service

**Official spec:** Fitness Machine Service v1.0
( https://www.bluetooth.com/specifications/specs/fitness-machine-service-1-0/ )
Adopted 2017-02-14. (PDF mirror: https://www.onelap.cn/pdf/FTMS_v1.0.pdf )

### 2.1 Service & characteristic UUIDs

| Name | 16-bit | Properties | Req. |
|------|--------|-----------|------|
| Fitness Machine Service | `0x1826` | Primary Service | — |
| Fitness Machine Feature | `0x2ACC` | Read | **M** |
| Treadmill Data | `0x2ACD` | Notify | O |
| Cross Trainer Data | `0x2ACE` | Notify | O |
| Step Climber Data | `0x2ACF` | Notify | O |
| Stair Climber Data | `0x2AD0` | Notify | O |
| Rower Data | `0x2AD1` | Notify | O |
| **Indoor Bike Data** | **`0x2AD2`** | Notify (also Read for summary) | O |
| Training Status | `0x2AD3` | Read, Notify | O |
| Supported Speed Range | `0x2AD4` | Read | C.1 |
| Supported Inclination Range | `0x2AD5` | Read | C.2 |
| Supported Resistance Level Range | `0x2AD6` | Read | C.3 |
| Supported Heart Rate Range | `0x2AD7` | Read | C.5 |
| Supported Power Range | `0x2AD8` | Read | C.4 |
| **Fitness Machine Control Point** | **`0x2AD9`** | **Write, Indicate** | O (Security = **Encryption**) |
| Fitness Machine Status | `0x2ADA` | Notify | C.6 (M if Control Point present) |

C.1–C.5: mandatory if the matching *Target Setting* feature bit is supported.
C.6: mandatory if the Control Point is supported.

> ⚠️ The Control Point requires an **encrypted link** (Table 4.1). ⇒ The client must
> **pair (and ideally bond)** before it can control the trainer. This is the #1 reason
> "Zwift can read my data but can't set resistance."

### 2.2 Fitness Machine Feature (0x2ACC) — 8 octets = two 32-bit fields

Structure (LSO…MSO): `[Fitness Machine Features : 4 octets][Target Setting Features : 4 octets]`.

#### 2.2.1 Fitness Machine Features field (first 32 bits)

| Bit | Feature |
|----:|---------|
| 0 | Average Speed Supported |
| 1 | Cadence Supported |
| 2 | Total Distance Supported |
| 3 | Inclination Supported |
| 4 | Elevation Gain Supported |
| 5 | Pace Supported |
| 6 | Step Count Supported |
| 7 | Resistance Level Supported |
| 8 | Stride Count Supported |
| 9 | Expended Energy Supported |
| 10 | Heart Rate Measurement Supported |
| 11 | Metabolic Equivalent Supported |
| 12 | Elapsed Time Supported |
| 13 | Remaining Time Supported |
| 14 | Power Measurement Supported |
| 15 | Force on Belt and Power Output Supported |
| 16 | User Data Retention Supported |
| 17–31 | RFU |

#### 2.2.2 Target Setting Features field (second 32 bits)

| Bit | Feature |
|----:|---------|
| 0 | Speed Target Setting Supported |
| 1 | Inclination Target Setting Supported |
| 2 | Resistance Target Setting Supported |
| 3 | Power Target Setting Supported |
| 4 | Heart Rate Target Setting Supported |
| 5 | Targeted Expended Energy Configuration Supported |
| 6 | Targeted Step Number Configuration Supported |
| 7 | Targeted Stride Number Configuration Supported |
| 8 | Targeted Distance Configuration Supported |
| 9 | Targeted Training Time Configuration Supported |
| 10 | Targeted Time in Two Heart Rate Zones Configuration Supported |
| 11 | Targeted Time in Three Heart Rate Zones Configuration Supported |
| 12 | Targeted Time in Five Heart Rate Zones Configuration Supported |
| 13 | Indoor Bike Simulation Parameters Supported |
| 14 | Wheel Circumference Configuration Supported |
| 15 | Spin Down Control Supported |
| 16 | Targeted Cadence Configuration Supported |
| 17–31 | RFU |

**Typical controllable smart-trainer feature value** (cadence + resistance + power +
power-target + resistance-target + simulation):
- FM Features = bit1(Cadence) | bit7(Resistance) | bit9(Energy) | bit12(Elapsed) |
  bit14(Power) = `0x4A82` (verify against your exact set)
- Target Features = bit2(Resistance target) | bit3(Power target) | bit13(Sim params) |
  bit14(Wheel circ) | bit15(Spin down) = `0xE00C`
- ⇒ 8-byte read = `82 4A 00 00  0C E0 00 00` (little-endian). **Compute your own from
  the table; this is an example, not a prescription.**

### 2.3 Indoor Bike Data (0x2AD2) — byte layout

Flags = `uint16` (2 bytes). Field transmission order (top→bottom = LSO→MSO):

| # | Field | Type | Size | Unit / Resolution | Present when |
|--:|-------|------|-----:|-------------------|--------------|
| — | Flags | uint16 | 2 | bitmask | always |
| 1 | Instantaneous Speed | uint16 | 2 | km/h ×0.01 (÷100) | bit0 (More Data) == 0 |
| 2 | Average Speed | uint16 | 2 | km/h ×0.01 | bit1 == 1 |
| 3 | Instantaneous Cadence | uint16 | 2 | rpm ×0.5 (÷2) | bit2 == 1 |
| 4 | Average Cadence | uint16 | 2 | rpm ×0.5 | bit3 == 1 |
| 5 | Total Distance | uint24 | 3 | metres ×1 | bit4 == 1 |
| 6 | Resistance Level | **sint16** | 2 | unitless ×0.1 | bit5 == 1 |
| 7 | Instantaneous Power | **sint16** | 2 | W ×1 | bit6 == 1 |
| 8 | Average Power | **sint16** | 2 | W ×1 | bit7 == 1 |
| 9 | Total Energy | uint16 | 2 | kcal ×1 | bit8 == 1 |
| 9 | Energy Per Hour | uint16 | 2 | kcal ×1 | bit8 == 1 |
| 9 | Energy Per Minute | uint8 | 1 | kcal ×1 | bit8 == 1 |
| 10 | Heart Rate | uint8 | 1 | bpm ×1 | bit9 == 1 |
| 11 | Metabolic Equivalent | uint8 | 1 | MET ×0.1 | bit10 == 1 |
| 12 | Elapsed Time | uint16 | 2 | s ×1 | bit11 == 1 |
| 13 | Remaining Time | uint16 | 2 | s ×1 | bit12 == 1 |

(When Expended Energy is present, all three energy sub-fields are sent together = 5 bytes.)

#### 2.3.1 Indoor Bike Data Flags (16-bit)

| Bit | Name | 0 ⇒ | 1 ⇒ |
|----:|------|-----|-----|
| 0 | **More Data** | **Instantaneous Speed PRESENT** | Instantaneous Speed **absent** |
| 1 | Average Speed present | absent | present |
| 2 | Instantaneous Cadence present | absent | present |
| 3 | Average Cadence present | absent | present |
| 4 | Total Distance Present | absent | present |
| 5 | Resistance Level Present | absent | present |
| 6 | Instantaneous Power Present | absent | present |
| 7 | Average Power Present | absent | present |
| 8 | Expended Energy Present | absent | present |
| 9 | Heart Rate Present | absent | present |
| 10 | Metabolic Equivalent Present | absent | present |
| 11 | Elapsed Time Present | absent | present |
| 12 | Remaining Time Present | absent | present |
| 13–15 | RFU | — | — |

> ⚠️ **Bit 0 is inverted** vs all the others. `More Data = 1` means "I'm sending *more*
> optional fields, so I dropped the mandatory Speed to fit the MTU." Many parsers get
> this backwards.
>
> ⚠️ **Bits 1 & 2 naming trap:** the SIG's `indoor_bike_data.xml` historically labelled
> bit 1 "Instantaneous Cadence" and bit 2 "Average Speed" — **swapped**. The FTMS spec
> text (Table 4.10) and the field *transmission order* both confirm **bit 1 = Average
> Speed**, **bit 2 = Instantaneous Cadence**. Use the spec text, not the old XML names.

#### 2.3.2 Canonical smart-trainer Indoor Bike Data packet

Common case: speed + cadence + resistance + power (no averages/energy/time).
Flags: bit2(cad)=1 →0x04, bit5(res)=1→0x20, bit6(pow)=1→0x40, bit0(More Data)=0 (speed present).
Flags = `0x0064`.
```
Offset 0-1 : Flags               = 64 00
Offset 2-3 : Instantaneous Speed = uint16 LE /100 → km/h
Offset 4-5 : Instantaneous Cadence = uint16 LE /2 → rpm
Offset 6-7 : Resistance Level    = sint16 LE (÷10 → unitless)
Offset 8-9 : Instantaneous Power = sint16 LE (W)
```
This is the layout Zwift/MyWhoosh/TrainerRoad read. (Confirms the oft-cited offsets
2/4/6/8 for speed/cadence/resistance/power.)

### 2.4 Fitness Machine Control Point (0x2AD9)

**Write + Indicate.** CCCD must enable indications. Requires **encrypted link**.
Every procedure except `Request Control` and `Reset` requires prior `Request Control`.

#### 2.4.1 Op Codes (uint8)

| Op | Name | Parameter (after op code) | Req |
|----:|------|---------------------------|-----|
| `0x00` | Request Control | none | M |
| `0x01` | Reset | none | M |
| `0x02` | Set Target Speed | UINT16 km/h ×0.01 | C.1 |
| `0x03` | Set Target Inclination | SINT16 % ×0.1 | C.2 |
| `0x04` | Set Target Resistance Level | UINT8 unitless ×0.1 | C.3 |
| `0x05` | Set Target Power | **SINT16** W ×1 | C.4 |
| `0x06` | Set Target Heart Rate | UINT8 bpm | C.5 |
| `0x07` | Start or Resume | none | M |
| `0x08` | Stop or Pause | UINT8: `0x01`=Stop, `0x02`=Pause | M |
| `0x09` | Set Targeted Expended Energy | UINT16 kcal | C.6 |
| `0x0A` | Set Targeted Number of Steps | UINT16 steps | C.7 |
| `0x0B` | Set Targeted Number of Strides | UINT16 strides | C.8 |
| `0x0C` | Set Targeted Distance | UINT24 metres | C.9 |
| `0x0D` | Set Targeted Training Time | UINT16 seconds | C.10 |
| `0x0E` | Set Targeted Time in Two HR Zones | array (see §4.16.2.15) | C.11 |
| `0x0F` | Set Targeted Time in Three HR Zones | array | C.12 |
| `0x10` | Set Targeted Time in Five HR Zones | array | C.13 |
| `0x11` | Set Indoor Bike Simulation Parameters | Simulation Param Array (below) | C.14 |
| `0x12` | Set Wheel Circumference | UINT16 mm ×0.1 | O |
| `0x13` | Spin Down Control | UINT8: `0x01`=Start, `0x02`=Ignore | O |
| `0x14` | Set Targeted Cadence | UINT16 rpm ×0.5 | C.15 |
| `0x15–0x7F` | RFU | | |
| `0x80` | Response Code | response payload | M |
| `0x81–0xFF` | RFU | | |

**Indoor Bike Simulation Parameter Array (Op 0x11)** — 6 bytes, LSO→MSO:

| Field | Type | Size | Unit | Resolution |
|-------|------|-----:|------|-----------|
| Wind Speed | sint16 | 2 | m/s | ×0.001 (÷1000) |
| Grade | sint16 | 2 | % | ×0.01 (÷100) |
| Crr (rolling resistance) | uint8 | 1 | — | ×0.0001 |
| Cw (wind resistance) | uint8 | 1 | kg/m | ×0.01 |

(Zwift sends grade + wind + crr + cw via this opcode for physics simulation.)

#### 2.4.2 Result Codes (uint8)

| Code | Meaning |
|-----:|---------|
| `0x00` | Reserved |
| `0x01` | Success |
| `0x02` | Op Code not supported |
| `0x03` | Invalid Parameter |
| `0x04` | Operation Failed |
| `0x05` | Control Not Permitted (forgot Request Control, or lost it) |
| `0x06–0xFF` | RFU |

#### 2.4.3 Response format (indicated)

```
[0x80][Request Op Code][Result Code][optional Response Parameter]
```
For `Spin Down` (0x13) Success, a Response Parameter follows (spin-down result, Table 4.22).
On ATT-level errors (CCCD not configured for indications, or a procedure already in
progress) the server returns an ATT error response **instead** of an indication.

### 2.5 Fitness Machine Status (0x2ADA) — Notify

Op Code (uint8) + optional Parameter. Mandatory if Control Point is supported.

| Op | Status | Parameter |
|----:|--------|-----------|
| `0x00` | RFU | — |
| `0x01` | Reset | — |
| `0x02` | Stopped or Paused by the User | Control Info (`0x01` Stop / `0x02` Pause) |
| `0x03` | Stopped by Safety Key | — |
| `0x04` | Started or Resumed by the User | — |
| `0x05` | Target Speed Changed | UINT16 km/h ×0.01 |
| `0x06` | Target Incline Changed | SINT16 % ×0.1 |
| `0x07` | Target Resistance Level Changed | UINT8 unitless ×0.1 |
| `0x08` | Target Power Changed | SINT16 W |
| `0x09` | Target Heart Rate Changed | UINT8 bpm |
| `0x0A` | Targeted Expended Energy Changed | UINT16 kcal |
| `0x0B` | Targeted Number of Steps Changed | UINT16 |
| `0x0C` | Targeted Number of Strides Changed | UINT16 |
| `0x0D` | Targeted Distance Changed | UINT24 m |
| `0x0E` | Targeted Training Time Changed | UINT16 s |
| `0x0F` | Targeted Time in Two HR Zones Changed | array |
| `0x10` | Targeted Time in Three HR Zones Changed | array |
| `0x11` | Targeted Time in Five HR Zones Changed | array |
| `0x12` | Indoor Bike Simulation Parameters Changed | Sim Param Array |
| `0x13` | Wheel Circumference Changed | UINT16 mm ×0.1 |
| `0x14` | Spin Down Status | Spin Down Status value |
| `0x15` | Targeted Cadence Changed | UINT16 rpm ×0.5 |
| `0x16–0xFE` | RFU | |
| `0xFF` | **Control Permission Lost** | — |

> `0xFF Control Permission Lost` is how the server tells the client it must re-issue
> `Request Control` (e.g., another client took over). Apps should re-request control on
> receipt.

### 2.6 Supported-* Range characteristics (Read, 6 bytes each)
- **Supported Resistance Level Range (0x2AD6):** `[min sint16][max sint16][increment sint16]` — unitless ×0.1.
- **Supported Power Range (0x2AD8):** `[min uint16][max uint16][increment uint16]` — W.
- Supported Speed Range (0x2AD4), Inclination Range (0x2AD5), Heart Rate Range (0x2AD7): analogous.

### 2.7 Common FTMS implementation mistakes
- Not advertising/exposing the Control Point with **Indicate** (must be Write+Indicate).
- Not requiring/establishing encryption → writes silently rejected or "Control Not Permitted".
- Forgetting `Request Control` (0x00) at session start.
- Using `0x20` as the response opcode (that's CPS). FTMS uses **`0x80`**.
- Treating Indoor Bike Data Power/Resistance as unsigned (both `sint16`).
- Inverting the "More Data" bit 0 logic.
- Sending Resistance as `sint16` for **Set Target Resistance Level (0x04)** — that param
  is actually **UINT8** (unitless ×0.1), even though the *reported* Resistance Level in
  Indoor Bike Data is `sint16`. Easy to mix up.
- Reporting Indoor Bike Data faster than the Control-Point/Status can keep up; ~1 Hz
  matches the spec's "typically once per second".

---

## 3. CPS Service (0x1818) — Cycling Power Service

**Official spec:** Cycling Power Service v1.1
( https://www.bluetooth.com/specifications/specs/cycling-power-service-1-1/ )

### 3.1 Service & characteristic UUIDs

| Name | 16-bit | Properties | Req. |
|------|--------|-----------|------|
| Cycling Power Service | `0x1818` | Primary Service | — |
| **Cycling Power Measurement** | **`0x2A63`** | Notify (Broadcast optional) | M |
| Cycling Power Feature | `0x2A65` | Read | M |
| Sensor Location | `0x2A5D` | Read | M |
| Cycling Power Control Point | `0x2A66` | Write, Indicate | O |
| Cycling Power Vector | `0x2A64` | Notify | O |

> ⚠️ **Corrections:** CP Control Point UUID is **`0x2A66`**, not `0x0066`. Sensor
> Location is the **same** `0x2A5D` characteristic/enum shared with CSC.

### 3.2 Cycling Power Measurement (0x2A63) — byte layout

Flags = `uint16` (2 bytes). Instantaneous Power is mandatory. Optional fields follow in
the fixed order below.

```
Offset  Size  Field                                            Type      Unit / Res        If flag bit
------  ----  -----------------------------------------------  --------  ----------------  -----------
0       2     Flags                                            uint16    bitmask           always
2       2     Instantaneous Power                              sint16    W (signed!)       always
4       1     Pedal Power Balance                              uint8     % ×0.5 (÷2)       bit0
5       2     Accumulated Torque                               uint16    N·m ×1/32 (÷32)   bit2
7       4     Wheel Rev Data – Cumulative Wheel Revolutions    uint32    rev               bit4
11      2     Wheel Rev Data – Last Wheel Event Time           uint16    1/2048 s          bit4   ← differs!
13      2     Crank Rev Data – Cumulative Crank Revolutions    uint16    rev               bit5
15      2     Crank Rev Data – Last Crank Event Time           uint16    1/1024 s          bit5   ← differs!
17      2     Extreme Force – Max                              sint16    N                 bit6
19      2     Extreme Force – Min                              sint16    N                 bit6
21      2     Extreme Torque – Max                             sint16    N·m ×1/32         bit7
23      2     Extreme Torque – Min                             sint16    N·m ×1/32         bit7
25      3     Extreme Angles (Max12 | Min12 packed in UINT24)  uint24    deg               bit8
28      2     Top Dead Spot Angle                              uint16    deg               bit9
30      2     Bottom Dead Spot Angle                           uint16    deg               bit10
32      2     Accumulated Energy                               uint16    kJ (×1000 J)      bit11
```
*(Offsets assume all preceding optionals are present; in practice you parse sequentially.)*

### 3.3 Cycling Power Measurement Flags (16-bit)

| Bit | Name | 0 | 1 |
|----:|------|---|---|
| 0 | Pedal Power Balance Present | absent | present |
| 1 | Pedal Power Balance Reference | Unknown | Left |
| 2 | Accumulated Torque Present | absent | present |
| 3 | Accumulated Torque Source | Wheel Based | Crank Based |
| 4 | Wheel Revolution Data Present | absent | present |
| 5 | Crank Revolution Data Present | absent | present |
| 6 | Extreme Force Magnitudes Present | absent | present |
| 7 | Extreme Torque Magnitudes Present | absent | present |
| 8 | Extreme Angles Present | absent | present |
| 9 | Top Dead Spot Angle Present | absent | present |
| 10 | Bottom Dead Spot Angle Present | absent | present |
| 11 | Accumulated Energy Present | absent | present |
| 12 | Offset Compensation Indicator | false | true |
| 13–15 | RFU | — | — |

### 3.4 Field notes & units (the traps)
- **Instantaneous Power = `sint16` SIGNED.** A value like `0xFFFF` = −1 W is valid
  (coasting/spindown). Treating it as `uint16` gives 65535 W.
- **Accumulated Torque = `uint16` UNSIGNED**, resolution 1/32 N·m (BinaryExponent −5).
  Rolls over; clients compute deltas.
- **Pedal Power Balance = `uint8`**, resolution 0.5 % (÷2). Represents the *left*-pedal
  share when bit1=1 (Left reference).
- **Last Wheel Event Time = 1/2048 s** (BinaryExponent −11). **Last Crank Event Time =
  1/1024 s** (BinaryExponent −10). **These differ within the same characteristic!**
  Event-time fields roll over every 32 s (wheel, 2^16/2048) and 64 s (crank).
- **Accumulated Energy = `uint16` kilojoules** (DecimalExponent +3 ⇒ J×10^3 = kJ).
- **Extreme Angles** are two 12-bit values packed into a 24-bit (3-byte) field:
  transmitted as `0x[MinAngle][MaxAngle]` (Max in low 12 bits, Min in high 12 bits).
  Example: Max=0xABC, Min=0x123 ⇒ transmitted `0x123ABC`.
- Cumulative Wheel Revolutions = `uint32` (may decrement, not below 0, not roll over).
  Cumulative Crank Revolutions = `uint16` (intended to roll over).

### 3.5 Cycling Power Feature (0x2A65) — 32-bit bitmask

| Bit | Feature |
|----:|---------|
| 0 | Pedal Power Balance Supported |
| 1 | Accumulated Torque Supported |
| 2 | Wheel Revolution Data Supported |
| 3 | Crank Revolution Data Supported |
| 4 | Extreme Magnitudes Supported |
| 5 | Extreme Angles Supported |
| 6 | Top and Bottom Dead Spot Angles Supported |
| 7 | Accumulated Energy Supported |
| 8 | Offset Compensation Indicator Supported |
| 9 | Offset Compensation Supported |
| 10 | Cycling Power Measurement Content Masking Supported |
| 11 | Multiple Sensor Locations Supported |
| 12 | Crank Length Adjustment Supported |
| 13 | Chain Length Adjustment Supported |
| 14 | Chain Weight Adjustment Supported |
| 15 | Span Length Adjustment Supported |
| 16 | Sensor Measurement Context (0 = Force based, 1 = Torque based) |
| 17 | Instantaneous Measurement Direction Supported |
| 18 | Factory Calibration Date Supported |
| 19 | Enhanced Offset Compensation Supported |
| 20–21 | Distribute System Support (2-bit): 0=Unspecified/legacy, 1=Not for distributed, 2=Can be used in distributed, 3=RFU |
| 22–31 | RFU |

**Minimal smart-trainer CPS (power + crank cadence):** Feature = bit3 (Crank Rev Data) =
`0x00000008`. Flags in measurement = bit5 (`0x0020`). Packet = `[20 00][power sint16][crev uint16][ctime uint16]` = 8 bytes.

### 3.6 Cycling Power Control Point (0x2A66) — Write + Indicate

**Op Codes (uint8):**

| Op | Name | | Op | Name |
|----:|------|-|----:|------|
| `0x00` | RFU | | `0x0C` | Start Offset Compensation |
| `0x01` | Set Cumulative Value | | `0x0D` | Mask CP Measurement Content |
| `0x02` | Update Sensor Location | | `0x0E` | Request Sampling Rate |
| `0x03` | Request Supported Sensor Locations | | `0x0F` | Request Factory Calibration Date |
| `0x04` | Set Crank Length | | `0x10` | Start Enhanced Offset Compensation |
| `0x05` | Request Crank Length | | `0x11–0x1F` | RFU |
| `0x06` | Set Chain Length | | `0x20` | **Response Code** |
| `0x07` | Request Chain Length | | `0x21–0xFF` | RFU |
| `0x08` | Set Chain Weight | | | |
| `0x09` | Request Chain Weight | | | |
| `0x0A` | Set Span Length | | | |
| `0x0B` | Request Span Length | | | |

**Response Value (uint8):** `0x01` Success · `0x02` Op Code not Supported ·
`0x03` Invalid Parameter · `0x04` Operation Failed. (Note: 0x00 is RFU here, unlike SCCP.)

**Response indication format:**
`[0x20][Request Op Code][Response Value][optional Response Parameter]`

> ⚠️ CPS response opcode = **`0x20`**; FTMS response opcode = **`0x80`**; SCCP response
> opcode = **`0x10`**. Three different control points, three different response codes —
> don't conflate them.

### 3.7 Common CPS implementation mistakes
- Reading Instantaneous Power as unsigned (must be `sint16`).
- Using 1/1024 for the wheel event time (CPS uses **1/2048** for wheel; 1/1024 for crank).
- Sending both CPS and CSC with the *same* event-time resolution and assuming clients
  handle it — Wahoo (historically) mis-blended them. PeloMon worked around this by
  reporting only power/energy in CPS and speed/cadence in CSC.
- Vector / torque accumulation errors: Accumulated Torque is unsigned and rolls over;
  clients must compute `(new - old) mod 2^16` before applying the 1/32 scale.
- Setting Distribute System Support bits wrong (it's a 2-bit enum, not a single flag).
- Forgetting Sensor Location (M in CPS, unlike CSC where it's conditional).

---

## 4. Device Information Service (0x180A)

**Spec:** Device Information Service v1.1
( https://www.bluetooth.com/specifications/specs/device-information-service-1-1/ )
MyWhoosh/Zwift/TrainerRoad all read DIS to label the trainer in the pairing screen.

| Name | 16-bit | Type | Notes |
|------|--------|------|-------|
| Manufacturer Name String | `0x2A29` | string (UTF-8) | e.g. "VeloTracker" |
| Model Number String | `0x2A24` | string | e.g. "VT-1" |
| Serial Number String | `0x2A25` | string | unique per device |
| Firmware Revision String | `0x2A26` | string | e.g. "1.0.3" |
| Hardware Revision String | `0x2A27` | string | e.g. "revB" |
| Software Revision String | `0x2A28` | string | optional |
| System ID | `0x2A23` | 8 bytes | optional |
| PnP ID | `0x2A50` | 7 bytes | optional but useful |
| IEEE 11073-20601 Regulatory Cert. Data List | `0x2A2A` | — | optional |

All DIS characteristics are **Read**, no security.

### PnP ID (0x2A50) layout — 7 bytes, LSO→MSO

| Field | Type | Size | Value |
|-------|------|-----:|-------|
| Vendor ID Source | uint8 | 1 | `1` = Bluetooth SIG Company ID; `2` = USB-IF Vendor ID |
| Vendor ID | uint16 | 2 | Company Identifier from Assigned Numbers (LE) |
| Product ID | uint16 | 2 | manufacturer-managed (LE) |
| Product Version | uint16 | 2 | manufacturer-managed (LE) |

> If you use Vendor ID Source = 1, the Vendor ID must be a real Bluetooth SIG-assigned
> Company Identifier (https://www.bluetooth.com/specifications/assigned-numbers/company-identifiers/).
> Using a random/wrong VID can cause some apps to mislabel or reject the device.

---

## 5. Battery Service (0x180F)

**Spec:** Battery Service v1.1
( https://www.bluetooth.com/specifications/specs/battery-service-1-1/ )

| Name | 16-bit | Type | Properties |
|------|--------|------|-----------|
| Battery Level | `0x2A19` | uint8 (0–100 %) | Read, (Notify optional) |

- Value 0–100 = percentage; the spec range is 0–100.
- Many trainers expose Battery Level at 100 % (or omit the service) since they're mains-powered.
- Zwift/MyWhoosh don't strictly require it, but it's commonly expected and harmless.

---

## 6. Advertising requirements for smart trainers

### 6.1 What MyWhoosh / Zwift / TrainerRoad actually look for
These apps scan the advertising packet and filter by **advertised Service UUIDs**. To be
detected as a controllable smart trainer you should advertise **`0x1826` (FTMS)**.
For maximum compatibility (some apps/parsers prefer CPS), also advertise **`0x1818` (CPS)**
and/or **`0x1816` (CSC)**. Real trainers (Wahoo KICKR, Tacx) typically advertise FTMS +
CPS; some also CSC.

| Purpose | Advertise |
|---------|-----------|
| Be discovered as a controllable trainer (Zwift/MyWhoosh/TR) | `0x1826` FTMS |
| Be discovered as a power meter | `0x1818` CPS |
| Be discovered as a speed/cadence sensor (Garmin watches) | `0x1816` CSC |

### 6.2 GAP Appearance — what to set
From the official Assigned Numbers (Appearance Sub-category values). **There is NO
"Fitness Machine" / "Indoor Bike" appearance code.** The Cycling category (`0x012`,
range `0x0480–0x04BF`):

| Value | Appearance |
|------:|-----------|
| `0x0480` | Generic Cycling |
| `0x0481` | Cycling: Cycling Computer |
| `0x0482` | Cycling: Speed Sensor |
| `0x0483` | Cycling: Cadence Sensor |
| `0x0484` | Cycling: **Power Sensor** ← use this for a smart trainer |
| `0x0485` | Cycling: Speed and Cadence Sensor |

Smart trainers normally set Appearance = `0x0484` (Cycling Power Sensor). Apps do **not**
rely on appearance for filtering (they filter on the Service UUID list); appearance only
affects the icon shown in some OS-level BLE scanners.

### 6.3 Advertising payload structure (AD types)
Per Core Spec Supplement, Part A. Each AD structure = `[length][type][data…]`.

| AD type | Value | Example |
|---------|------:|---------|
| Flags | `0x01` | `02 01 06` (LE General Discoverable + BR/EDR not supported) |
| Complete List of 16-bit Service UUIDs | `0x03` | `05 03 26 18 18 18` (FTMS + CPS) |
| Incomplete List of 16-bit Service UUIDs | `0x02` | use when more may follow |
| Complete Local Name | `0x09` | `0A 09 56 65 6C 6F 54 72 61 63 6B 65 72` ("VeloTracker") |
| Shortened Local Name | `0x08` | when name doesn't fit |
| TX Power Level | `0x0A` | signed int8 dBm |
| Manufacturer Specific Data | `0xFF` | `[2-byte Company ID LE][data…]` |

- Service UUIDs are transmitted **little-endian**: FTMS `0x1826` → `26 18`; CPS `0x1818`
  → `18 18`; CSC `0x1816` → `16 18`.
- If you advertise the same service list in the Scan Response, you can keep the primary
  advert smaller.

### 6.4 Manufacturer data (Wahoo / Garmin / FE-C)
- **Wahoo** trainers put proprietary manufacturer data (Company ID `0x0247` Wahoo) in the
  advert/scan response for the legacy "Wahoo KICKR" protocol. Modern apps use FTMS, so
  you do **not** need to emulate Wahoo manufacturer data for FTMS-based detection.
- **Garmin FE-C** (ANT+ FE-C over BLE) is a separate, older proprietary scheme and is not
  required for FTMS-compatible apps.
- For a pure FTMS simulator, **omit manufacturer-specific data** — it only complicates
  the 28-byte budget and isn't used by Zwift/MyWhoosh/TrainerRoad.

### 6.5 TX power considerations
- Set TX Power AD (`0x0A`) optionally (1 byte). Helps some clients estimate range.
- Typical smart-trainer TX power: 0 to +4 dBm. Higher = more reliable in a noisy 2.4 GHz
  environment (trainers are often near other devices). +4 dBm is a safe default.
- If you include the TX Power AD, subtract 1 byte from your payload budget.

### 6.6 macOS CoreBluetooth advertising limit ⚠️
- BLE advert max is 31 bytes (Core 4.x). **CoreBluetooth on macOS/iOS effectively limits
  the usable advertising payload to ~28 bytes** (it reserves ~3 bytes for the mandatory
  Flags AD and overhead). The Scan Response has a separate ~28-byte budget.
- **Practical budgeting (primary advert):**
  - Flags: 3 B
  - Complete List of 16-bit SVCS (FTMS+CPS): 2 + 4 = 6 B → **9 B used**
  - TX Power: 2 B → 11 B
  - Complete Local Name "VeloTracker" (10 chars): 12 B → **23 B** ✅ fits
  - A longer name (e.g. "VeloTracker Pro 1234") won't fit → use Shortened Local Name in
    the advert and the full name in the Scan Response.
- **Never advertise 128-bit service UUIDs** (18 B each) for these standard 16-bit
  services — you'll blow the budget instantly. Always use the 16-bit forms.

---

## 7. Common pairing/connection issues & how commercial trainers solve them

### 7.1 FTMS Control Point "request control" flow
1. Client connects, discovers services, enables indications on Control Point (0x2AD9) CCCD
   and notifications on Indoor Bike Data (0x2AD2) / Fitness Machine Status (0x2ADA).
2. Client writes `0x00` (Request Control). Server indicates `[0x80][0x00][0x01]` (Success).
3. Only now may the client issue Set Target Power / Resistance / Simulation / Start-Stop.
4. Control is held until: disconnect, a `Reset` (0x01), or the server sends Status `0xFF`
   (Control Permission Lost) — e.g. another client connected and took control.
5. On reconnect, re-issue Request Control.

> Many "can't control the trainer" reports boil down to: (a) link not encrypted, or
> (b) Request Control never sent, or (c) Control Point CCCD not set for indications.

### 7.2 Notification intervals recommended by spec
- **CSC Measurement:** ~1 Hz (CSCS §3.1.1.4 "approximately once per second").
- **Indoor Bike Data:** ~1 Hz (FTMS §4.9.1 "typically once per second").
- **Cycling Power Measurement:** commonly 1 Hz; many power meters notify at 1 Hz and some
  at higher rates. Apps compute deltas, so faster ≠ better and wastes bandwidth.
- A Data Record that exceeds the ATT_MTU is split across multiple notifications (FTMS §4.1,
  §4.19). If split, Elapsed Time must be included and the "More Data" bit governs the
  mandatory Speed field. For a trainer, keep each record in one notification.

### 7.3 GATT signed writes
- None of these specs require **Signed Writes** (Write Without Response + signature).
- FTMS/CPS/SCCP Control Points use the **Write Characteristic Value** sub-procedure =
  *Write with Response*. The server then **Indicates** the result.
- Some clients mistakenly use Write Without Response; a compliant server should still
  accept a normal Write. Do not rely on signed writes for security — use link encryption.

### 7.4 Bonding expectations
- **FTMS Control Point requires encryption** ⇒ the client must pair. Without bonding,
  every reconnection re-pairs (slow, and on iOS can prompt). **Bonding is strongly
  recommended** so the LTK is stored and reconnection is seamless.
- After bonding, ensure your GATT layout is **stable across connections** (handles can
  move on some stacks, but service/characteristic UUIDs must remain). Avoid changing the
  service set between firmware revisions without a Service Changed indication.
- CoreBluetooth quirks: macOS caches services aggressively; if you change your GATT table,
  the client may need to "forget device" or you must indicate Service Changed (0x2A05).

### 7.5 Other recurring gotchas seen with real trainers
- **KICKR Core** omits the Request Op Code field in some unsupported-opcode responses —
  don't copy that; always send `[0x80][req op][0x02]`.
- **Some bikes** send Indoor Bike Data fields in the wrong order or with the wrong
  signedness (documented on r/bluetoothlowenergy). Be the spec-compliant one.
- Zwift reads **both** FTMS (Indoor Bike Data) and CPS (Cycling Power Measurement) on
  some trainers and can double-count if both advertise overlapping data. Pick one primary
  power source; if you expose both, keep them consistent (same power value).
- For **ERG mode**, TrainerRoad/Zwift use `Set Target Power` (0x05). Your trainer must
  honour it and reflect the new target via Indoor Bike Data power + Status `0x08`
  (Target Power Changed) — though Status is for *user-initiated* changes; app-set targets
  are simply acknowledged via the Control Point response.

---

## 8. Audit checklist for the VeloTracker implementation

Use this as a literal checklist when reviewing the code:

**CSC (if implemented)**
- [ ] CSC Measurement flags = uint8; bit0=wheel, bit1=crank; RFU bits = 0.
- [ ] Cumulative Wheel Revolutions = **uint32** (4 B); never rolls over; ≥0.
- [ ] Last Wheel/Crank Event Time = uint16, **1/1024 s** each.
- [ ] Cumulative Crank Revolutions = uint16 (rolls over).
- [ ] CSC Feature = uint16; only bits 0–2 defined.
- [ ] Sensor Location = uint8 enum 0–16 (no "top of chain").
- [ ] SCCP present with Write+Indicate; handles 0x01/0x03/0x04; responds with 0x10.
- [ ] Notifications ~1 Hz.

**FTMS (primary)**
- [ ] Service 0x1826; Feature 0x2ACC Read (8 B); Indoor Bike Data 0x2AD2 Notify.
- [ ] Control Point 0x2AD9 = **Write+Indicate**, security = **Encryption**.
- [ ] Fitness Machine Status 0x2ADA Notify (mandatory if CP present).
- [ ] Feature bitmask built correctly from the two 32-bit tables (§2.2).
- [ ] Indoor Bike Data flags uint16; **bit0 More Data inverted**; bit1=Avg Speed,
      bit2=Inst Cadence (NOT the swapped XML names).
- [ ] Speed uint16/100; Cadence uint16/2; Resistance **sint16**; Power **sint16**;
      Total Distance uint24; Energy = Total(uint16)+PerHour(uint16)+PerMinute(uint8).
- [ ] Control Point: Request Control(0x00) gating; Reset(0x01); Set Target Power(0x05)
      param = **sint16**; Set Target Resistance(0x04) param = **uint8**; Sim params(0x11)
      = wind(sint16/1000) + grade(sint16/100) + crr(uint8) + cw(uint8).
- [ ] Response = `[0x80][req op][result]`; result 0x01–0x05.
- [ ] Supported Power Range 0x2AD8 + Supported Resistance Level Range 0x2AD6 readable.

**CPS (if implemented alongside FTMS)**
- [ ] Measurement 0x2A63 flags uint16; Instantaneous Power **sint16**.
- [ ] Wheel event time **1/2048 s**; crank event time **1/1024 s** (different!).
- [ ] Accumulated Torque **uint16**/32; Pedal Balance uint8/2; Energy uint16 kJ.
- [ ] Feature 0x2A65 = uint32 (bits per §3.5; Distribute System = 2-bit enum).
- [ ] Control Point 0x2A66 responds with opcode **0x20**.
- [ ] Sensor Location 0x2A5D present (mandatory in CPS).

**DIS / Battery / Advertising**
- [ ] DIS 0x180A with 0x2A29/0x2A24/0x2A25/0x2A26/0x2A27 (strings, Read).
- [ ] PnP ID 0x2A50 = 7 B (source/vid/pid/ver); Vendor ID Source ∈ {1,2}.
- [ ] Battery 0x180F / 0x2A19 uint8 0–100.
- [ ] Advertise 16-bit SVCS (0x1826 [+0x1818] [+0x1816]) little-endian; ≤28 B on macOS.
- [ ] Appearance = 0x0484 (Cycling Power Sensor). No "Fitness Machine" appearance.
- [ ] Flags AD = 0x06; Local Name fits or use Scan Response for the full name.

**Pairing / control flow**
- [ ] Pairing/bonding enabled (CP requires encryption).
- [ ] Client must Request Control before Set Target *; server enforces (Result 0x05).
- [ ] CCCDs: Indications on Control Points; Notifications on Data/Status.
- [ ] Reconnect re-issues Request Control; handle Status 0xFF (Control Permission Lost).

---

### Reference document numbers
- CSCS v1.0 — adopted; service spec on bluetooth.com.
- CPS v1.1 — adopted 2014-07-02 (characteristic XMLs) / service spec.
- FTMS v1.0 — adopted 2017-02-14 (D10r01 → V1.0).
- GATT Specification Supplement (GSS) — current version on
  btprodspecificationrefs.blob.core.windows.net.
- Assigned Numbers — appearance values, company identifiers, service/characteristic UUIDs.
- All characteristic byte layouts above are taken from the SIG-assigned
  `org.bluetooth.characteristic.*.xml` definitions (the authoritative machine-readable
  source), cross-checked against the service-spec prose.
