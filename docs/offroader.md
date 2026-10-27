# Überarbeiteter Offroader

Das originale Expeditionsfahrzeug besitzt eine durchgehend geformte Motorhaube, einen sichtbaren Motorraum und ein detailliertes Cockpit für Nahansichten. Die Ausgangsmaße bleiben erhalten: 2,64 m Radstand, 1,84 m Spurweite und 0,86 m Reifendurchmesser. Geometrie, Beschriftungen und Oberflächentexturen entstehen lokal im Python-Generator; es werden keine fremden Fahrzeugmodelle, Logos oder Bildtexturen eingebunden.

| Bisherige Fassung | Überarbeitete Fassung |
| --- | --- |
| ![Bisherige Außenansicht](previews/offroader_before.png) | ![Überarbeitete Außenansicht](previews/offroader.png) |

| Motorraum bei geöffneter Haube | Blick aus der Kabine |
| --- | --- |
| ![Modellierter Motorraum](previews/offroader_engine.png) | ![Cockpit mit Instrumenten und Pedalen](previews/offroader_cockpit.png) |

![Innenraum als Schnittansicht: äußere Karosserieteile sind nur für dieses Bild ausgeblendet](previews/offroader_interior.png)

Die Schnittansicht blendet Dach, Scheiben und zugehörige Anbauteile nur im Render aus. Im Blender-Modell und in den GLBs sind sie vorhanden. Die Cockpitansicht stammt aus der zusammengebauten Kabine. Alle Bilder werden aus den tatsächlichen Modellflächen gerendert.

## Erzeugen und prüfen

```bash
python3 generate_vehicles.py --asset offroader --output generated --render --resolution 1200 --samples 192 --texture-size 512
blender -b --python-exit-code 1 --python tools/render_offroader_details.py -- --asset-dir generated/offroader
python3 tools/validate_exports.py generated/offroader
blender -b --python-exit-code 1 --python tools/smoke_blender.py -- generated/offroader
```

Bei einem bereits gefüllten Ausgabeordner muss der Generator zusätzlich `--force` erhalten. Der Detailrenderer prüft den Hash der Quelldatei, erzeugt drei PNGs, trägt ihre Hashes und die Renderprovenienz im Manifest ein und verändert die Quelldatei nicht.

## Bauteile

- **Haube:** gewölbte Blechform mit integrierten Schultern, umlaufender Kante, Unterseitenverstärkungen, Dämmung, Scharnieren und Schloss.
- **Scheinwerfer:** Reflektorschalen, Projektorlinsen, äußere Abdeckungen, Dichtungen und getrennte kleine Lichtquellen. Der Kühler ist hinter den tatsächlichen Grillöffnungen modelliert.
- **Motorraum:** Reihenvierzylinder mit Kopf/Deckel, Ölwanne, Ansaug- und Abgaswegen, Riementrieb, Lichtmaschine, Luftfilter, Kühler/Lüfter, Batterie, Sicherungskasten, Flüssigkeitsbehältern und Leitungen.
- **Cockpit:** manuelle Sechsgangschaltung, Verteilergetriebehebel, Handbremse und drei Pedale; beschriftete Rundinstrumente, originale statische Routengrafik, Klimaregler, Schalter und Luftausströmer.
- **Kabine:** fünf gepolsterte Sitze, Stoffeinlagen und Nähte, Gurte, Türablagen, Fensterheberschalter, Lautsprecher, Innenspiegel, Sonnenblenden und Haltegriffe.

## Drehpunkte im Blender-Quellmodell

| `part_role` | Lokale Achse | Vorgesehene Bewegung |
| --- | --- | --- |
| `hood_hinge_pivot` | Y | 0 bis −72°, Haube öffnen |
| `cabin_steering_pivot` | X | −540 bis +540°, geneigte Lenksäule |
| `gear_lever_pivot`, `transfer_lever_pivot` | Y | −12 bis +12°, Hebel vor/zurück |
| `handbrake_pivot` | Y | 0 bis −25°, anheben |
| `accelerator_pivot`, `brake_pivot`, `clutch_pivot` | Y | 0 bis −18°, treten |
| vier `*_wheel_pivot` | Y | Radrotation |
| zwei `front_*_steering_pivot` | Z | Vorderradlenkung |

Die Engine muss diese Bewegungen an Eingaben koppeln und Achsen nach dem Import prüfen. Ein seitliches Schaltgassenverhalten kann zusätzlich am Ganghebel angesteuert werden; es gibt dafür keine fertige Schaltsimulation. Manschetten sind statisch und benötigen bei großen Hebelbewegungen eine eigene Verformung. Türen und Fahrwerksfederung sind nicht animiert. Der Motor ist sichtbare Geometrie; innere Kolben, Verbrennung, Fahrphysik und Sounds werden nicht simuliert.

## Darstellung und Budget

Der Lack verwendet `KHR_materials_clearcoat`, Scheiben und Scheinwerfer `KHR_materials_transmission`. Oberflächenkörnung und Gewebe liegen als eingebettete Bildtexturen vor. Dezente Staubspuren an unteren Außenflächen sind als Vertexfarben gespeichert. Kleine Beschriftungen, Schrauben und Gitterdetails verschwinden gezielt aus entfernten LODs; weitere Flächen werden vereinfacht. Alle beweglichen Baugruppen bleiben erhalten. Die tatsächlichen Dreiecks-, Mesh- und Dateigrößen stehen im jeweiligen Manifest.

Die automatische UV-Verteilung ist kein eindeutiger Texturatlas. Ein finales Spielbudget hängt von Sichtabstand, Anzahl gleichzeitig sichtbarer Fahrzeuge und Engine ab. Der Blender-Roundtrip prüft Geometrie, Texturen und Drehpunkte; ein Godot-/Unreal-/Unity-Laufzeittest ist damit nicht ersetzt.
