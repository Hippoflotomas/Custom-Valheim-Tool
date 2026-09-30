Valheim Pack Builder
====================

Makes shield and banner packs for the ShieldShare and BannerShare Valheim mods:
fill in a form, add your pictures, and export a zip that drops straight into the mod's folder.

No install needed, and you don't need Python.


STARTING IT
-----------
1. Unzip the whole ValheimPackBuilder folder somewhere, e.g. your Desktop or Documents.
   Keep everything in the folder together: the program needs the files next to it.
2. Double-click ValheimPackBuilder.exe.

"Windows protected your PC": the program isn't code-signed (that costs money every year),
so Windows warns about it until enough people have run it. Click "More info", then
"Run anyway".


MAKING A PACK
-------------
1. New shield pack, or New banner pack. A pack holds only shields or only banners.
2. Fill in the name. The ID is filled in for you; don't change it once people are using the pack,
   because placed banners and crafted shields remember it.
3. Add your picture(s). Drag to move, scroll to zoom, Shift+scroll or the Rotate buttons to turn it.
   On shields the brown shows plain wood: transparent parts of your picture stay transparent.
4. Export zip, then put the zip in:
     Shields:  Documents\Valheim Custom Shields
     Banners:  Documents\Valheim Custom Banners

Open pack... reads a pack zip back in, so you can fix it later. The zip is the save file.


SHIELDS NEED THE MOD'S PATTERN GUIDES
-------------------------------------
To cut your picture to the shield's shape, the program reads ShieldShare's own guides from
  Documents\Valheim Custom Shields\_Templates
ShieldShare writes the Wood shield guide every time the game starts with the mod installed.
Guides for the other shields only appear after a pack using that shield has been loaded
once. If your guides are somewhere else, use File > Pattern guide folder...


IF SOMETHING GOES WRONG
-----------------------
Error details are saved to
  %LOCALAPPDATA%\ValheimPackBuilder\error.log
(paste that into File Explorer's address bar). Please include that file if you report a problem.


Help > About shows the version. Licences for the parts this program is built from are in
THIRD-PARTY-NOTICES.txt and the "licenses" folder.
