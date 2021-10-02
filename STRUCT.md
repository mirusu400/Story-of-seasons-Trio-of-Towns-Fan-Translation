# WARNING
All of these are as of *Japanese* version of TOT, It may different structure of USA, EUR version.

# PAPA file
All of PAPA files are not have extension, but they have magic stamp(header) `PAPA`.

Also, all of these are `Little Endian`.
```
Offset  Size                        Info                    Hex
0x00    ascii                       Magic stamp [PAPA]      50 41 50 41
0x04    0x08                        Unknown                  00 00 00 00 0C 00 00 00  
0x0C    0x04                        Size    
0x10    0x04                        Count of subfile
0x14    0x04 * (Count of subfile)   Array of *block* offset


On a block offset..
Offset      Size        Info                                            Hex
0x00        0x04        block size
0x04        0x04        USA = 2, JPN = 3
0x08        0x04        Offset of "Msg " flag (Normally Points 0x14)
0x0C        0x04        Offset of "Text" (Normally Points 0x18)
0x10        0x04        Offset of "Label"
0x[Msg]     ascii       characters "Msg "                               4D 73 67 00
0x[Text]    unicode     Text (Encoding: Unicode)
0x[Label]   ascii       Flag (Encoding: Ascii) 
end         0x04        EOF flag                                        00 00 00 00

WARNING:
File sizes may be mod by 4.
```