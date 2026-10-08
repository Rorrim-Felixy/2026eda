* Fresh ASM-HEMT synthetic truth run; first SPICE line is a title.
simulator lang = spectre
global 0

ahdl_include "asmhemt_nano_isothermal.va"

model asm_truth asmhemt \
    shmod=0 \
    trapmod=1

parameters VGS=0 VDS=5 TEMP_C=27
simulatorOptions options temp=TEMP_C tnom=27 reltol=1e-4 rawfmt=psfascii

Pg (g 0) port r=50 num=1 dc=VGS mag=1
Pd (d 0) port r=50 num=2 dc=VDS mag=0

Xdut (d g 0 0) asm_truth

dcVg dc param=VGS start=-5 stop=2 step=0.1
dcVd dc param=VDS start=0 stop=10 step=0.1

save Pg:p Pd:p
