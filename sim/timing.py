"""Timing parameters (ns) used by the part models.

Values are representative 5 V datasheet figures for the exact families named
in the spec's BOM.  Propagation ranges are (min, max) where the max is the
-40..85 C limit at 4.5 V / 50 pF and the min is a conservative fast-corner
guess (datasheets rarely give one).  Each part instance draws a fixed delay
uniformly from its range per seed, so Monte Carlo runs explore skew between
paths.  Edit here and re-run; nothing else hard-codes timing.
"""

# combinational / buffer families: tpd (min, max)
TPD = {
    "HC14":   (4, 31),
    "AC14":   (1.5, 10),
    "HCT04":  (4, 29),
    "HC30":   (5, 38),
    "HCT00":  (4, 30),
    "HCT08":  (4, 30),
    "HCT32":  (4, 30),
    "HC10":   (4, 30),
    "HCT138": (6, 45),
    "HCT157": (6, 40),
    "HCT244": (5, 35),
    "HCT541": (5, 30),
    "RSER":   (0, 0),
}

# three-state enable / disable (min, max)
TEN = {"HCT541": (8, 45), "HCT573": (8, 45), "HCT244": (8, 38), "HC595": (8, 38)}
TDIS = {"HCT541": (8, 45), "HCT573": (8, 40), "HCT244": (8, 38), "HC595": (8, 38)}

# sequential parts
QQN_SKEW = 3        # assumed max Q-vs-/Q skew within one HCT74 flip-flop
HCT74 = dict(clk_q=(6, 44), clr_q=(6, 50), tsu=25, th=3)
HCT273 = dict(clk_q=(7, 40), clr_q=(7, 45), tsu=20, th=5)
HCT573 = dict(d_q=(7, 40), le_q=(7, 45), tsu=20, th=10)
HC595 = dict(clk_q=(6, 38), clr_q=(6, 38), tsu=25, th=5, oe=(8, 38))

# AS6C62256-55 SRAM
SRAM = dict(tAA=55, tACE=55, tOE=30, tOLZ=5, tCLZ=10, tOH=10, tOHZ=(2, 20),
            tWP=45, tDW=25, tDH=0, tAS=0, tWR=0)

# AT27C256R-70 OTP EPROM (slower of the two candidate speed grades)
EPROM = dict(tACC=70, tCE=70, tOE=30, tOH=0, tDF=(2, 25))

# W65C02S at 5 V
CPU = dict(tADS=(10, 30),   # address / RWB valid after PHI2 fall (min is tAH)
           tMDS=(10, 30),   # write data valid after PHI2 rise
           tDHW=(10, 30),   # write data hold (release) after PHI2 fall
           tDSR=10,         # read data setup before PHI2 fall
           tDHR=10,         # read data hold after PHI2 fall
           tPW=35)          # minimum PHI2 high / low width
