# Enduro, Helikopter, Flugzeug, 4×4 & Motorboot – Blender-Python-Generator

Ein eigenständiges Python-Skript erzeugt fünf detaillierte, markenfreie Fahrzeugmodelle: eine Sport-Enduro, einen leichten Mehrzweckhelikopter, ein einmotoriges Sport-/Reiseflugzeug, einen viertürigen 4×4-Geländewagen und ein offenes Sportmotorboot mit Außenborder. Alle Geometrien und Oberflächentexturen entstehen lokal; Modell-Downloads, API-Schlüssel und kostenpflichtige Dienste werden nicht benötigt.

| Motorboot | Geländewagen | Flugzeug |
| --- | --- | --- |
| ![Mit dem Skript erzeugtes Motorboot, in Blender gerendert](docs/previews/motorboat.png) | ![Mit dem Skript erzeugter 4×4, in Blender gerendert](docs/previews/offroader.png) | ![Mit dem Skript erzeugtes Flugzeug, in Blender gerendert](docs/previews/airplane.png) |

**Voraussetzung:** Blender **4.3 oder neuer** mit Cycles und dem mitgelieferten glTF-Exporter. Getestet mit Blender 4.3.2. Zusätzliche Python-Pakete müssen nicht installiert werden.

## Start

Im Repository-Verzeichnis:

```bash
python3 generate_vehicles.py --asset all --output generated --render
```

Das Skript startet `blender` aus dem `PATH`. Alternativ den Blender-Pfad direkt verwenden, zum Beispiel unter Windows `"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"` anstelle von `blender`:

```bash
blender --background --factory-startup --python-exit-code 1 --python generate_vehicles.py -- --asset all --output generated --render
```

Der Generator läuft absichtlich in einem eigenen Hintergrundprozess. Jede Asset-Szene wird dort neu angelegt. Eine bereits geöffnete Blender-Arbeitssitzung bleibt erhalten.

Nur ein Fahrzeug, mehr Samples für die Vorschau und größere Oberflächentexturen:

```bash
python3 generate_vehicles.py --asset enduro --output generated_detail --texture-size 512 --samples 96 --resolution 1600 --render
```

Nur das neue Motorboot erzeugen:

```bash
python3 generate_vehicles.py --asset motorboat --output generated --render
```

Die Einzelmodelle heißen `enduro`, `helicopter`, `airplane`, `offroader` und `motorboat`. `--asset all` ist der Standard und erzeugt alle fünf Modelle; `--asset both` bleibt kompatibel und erzeugt weiterhin ausschließlich Enduro und Helikopter. Ohne `--render` werden keine Vorschaubilder berechnet. `--lods 1`, `2` oder `3` bestimmt die Anzahl der Detailstufen; Standard ist `3`. `--seed 7` steuert reproduzierbar die Mikrostruktur der Materialien, nicht die Fahrzeugform. Bestehende, nicht leere Asset-Ordner werden nur mit `--force` verwendet. Dabei werden bekannte Ausgabedateien überschrieben und nicht mehr angeforderte LODs/Vorschaubilder entfernt; andere Dateien bleiben erhalten.

## Ergebnis

Pro Fahrzeug entsteht ein Unterordner:

| Datei | Inhalt |
| --- | --- |
| `<asset>.blend`, z. B. `motorboat.blend` | Bearbeitbare Bauteile, Drehpunkte, Materialien, versteckte LODs/Kollisionskörper und Renderstudio |
| `*_LOD0.glb` | Vollständige Geometrie, für den Export nach Material und Bewegungsgruppe zusammengefasst |
| `*_LOD1.glb`, `*_LOD2.glb` | Automatisch vereinfachte Geometrie; tatsächliche Dreieckszahlen im Manifest |
| `*_collision.glb` | Separate grobe, konvexe Box-Kollisionskörper |
| `*_preview.png` | Optionales Cycles-Studiobild |
| `manifest.json` | Maße, Dreieckszahlen, Herkunft, Generator-Hash, Dateigrößen, SHA-256-Prüfsummen und Grenzen des Modells |

Die `.blend`-Datei zeigt die detaillierte `*_SOURCE`-Collection. Die `*_LOD…`-Collections und Kollisionskörper sind zunächst ausgeblendet. `STUDIO_RENDER_ONLY` enthält Kamera, Beleuchtung und Boden und gehört nicht zum GLB-Export.

Beim Motorboot enthält das Studio eine Wasserfläche auf Z = 0. Sie dient nur der Vorschau und gehört nicht zum exportierten Fahrzeug. `STUDIO_RENDER_ONLY` ausblenden, um den Unterwasserrumpf und den Propeller vollständig zu betrachten.

Die Enduro enthält Speichenräder, modellierte Stollen, Bremsscheiben mit Aussparungen, Motor/Kühlrippen, Rahmen, Federbein, Schwinge, Kette, Auspuff, Verkleidung, Sitzbank, Lenker und Leitungen. Räder und Lenkung haben eigene Drehpunkte. Der Helikopter enthält einen geformten Rumpf, dunkle Cockpit- und Türverglasung, Dichtungen, Kufen, Turbinenverkleidung, Leitwerke, profilierten Haupt-/Heckrotor und separate Rotor-Drehpunkte. Seine Verglasung ist **opak**; eine ausgearbeitete Innenkabine ist nicht enthalten.

Das Flugzeug ergänzt einen geformten Rumpf, profilierte Tragflächen, Dreiblattpropeller mit Spinner, Fahrwerk mit drei Rädern, Querruder, Landeklappen, Höhen- und Seitenruder sowie Cockpitverglasung, Lufteinlässe und Positionslichter. Zehn Drehpunkte trennen Propeller, Räder und Steuerflächen für spätere Animationen. Auch hier ist die Verglasung **opak** und es gibt keine ausgearbeitete Innenkabine oder Flugsimulation.

Der Geländewagen enthält offene Radausschnitte, profilierte Reifen, ein Reserverad, Leiterrahmen, Achsen, Differentiale, Antriebswellen, Federn und Dämpfer. Die geformte Motorhaube lässt sich öffnen. Darunter liegen ein modellierter Reihenvierzylinder, Ansaugung, Riementrieb, Kühler mit Lüfter, Batterie, Behälter und Leitungen. Die Front besitzt einen offenen Kühlergrill sowie runde Scheinwerfer mit Reflektoren, Projektorlinsen, transparenter Abdeckung und schmalem Tagfahrlichtring. Lack mit Klarlackschicht, Seilwinde, Schnorchel und Dachträger ergänzen die Karosserie.

Leicht gewölbte Türflächen, eine nach oben verjüngte Kabine, weichere Haubenschultern und um die Ecken geführte Stoßfänger formen die Karosserie. Dünnere Dachkanten, schlankere Dachträger, abgerundete Fensterecken und weich geformte Radläufe ergänzen die Silhouette. Vertiefte Türfugen und in die Front eingesetzte Scheinwerfer bleiben erhalten. Alle vier Türen und die seitlich angeschlagene Hecktür lassen sich um eigene Drehpunkte öffnen. Sie tragen ihre Scheiben, Dichtungen, Griffe und Innenverkleidungen mit; Reserverad, Heckwischer und Heizdrähte folgen der Hecktür. Türöffnungen mit Blechfalzen, Scharnieren und Schließbügeln sowie ein flacher Gepäckraum mit Teppich, Verzurrösen und Seitennetzen ergänzen die zugängliche Kabine. Reifenprägung und Polsternähte verwenden eigene Normalmaps; die Profilblöcke folgen den Reifenschultern. Im Motorraum sitzen ausgerichtete Schlauchschellen, Spulenstecker und zusätzliche Halterungen.

Die Haube trägt einen erhöhten Mittelbereich und eine echte Luftöffnung mit fünf Lamellen und einem Kanal darunter. Geformte obere Türkanten ersetzen die breiten Fensterleisten; die gerundete Front ist mit den festen Seitenwänden zu einer durchgehenden Schale verbunden. Dreieckige Spiegelaufnahmen und schmale Glasdichtungen ergänzen die Übergänge. Die Scheinwerfer besitzen dünne gewölbte Abdeckungen vor getrennten Projektoren und Reflektoren; sechs Blinker erhalten transparente Prismenabdeckungen, Reflektormulden und eigene Gehäuseränder.

Der Innenraum enthält zwei konturierte Vordersitze und eine durchgehende Rückbank mit Oberschenkelauflage, Sitzmulden und Lendenkontur. Sitzkissen und Lehnen bestehen jeweils aus einer geschlossenen Polsterform mit bündigen Stoff- und Lederbereichen. Die Mittelkonsole besitzt eine längs und quer geformte Kontur mit einer zentralen gepolsterten Armlehne; Kopfstützen und Sitzkanten sind weicher geformt. Das Armaturenbrett besitzt gerundete Übergänge, eine gewölbte Instrumentenhaube, einen vertieften Bildschirm und eingefasste Bedienelemente. Beschriftete Instrumente, eine statische Navigationsanzeige, Klimaregler, Luftausströmer, Gurte und Türlautsprecher ergänzen die Ausstattung. Lenkrad, Gang- und Verteilergetriebehebel, Handbremse, Gas, Bremse und Kupplung haben getrennte Drehpunkte; die Sechsgangbeschriftung enthält auch den Rückwärtsgang. Federung und Schaltmanschetten bleiben statisch; Fahrphysik, Türsteuerung und interaktive Anzeigen müssen in der Engine eingerichtet werden. Die Kabinenscheiben besitzen modellierte Stärke und dezent grüne Glaskanten. Scheiben/Linsen verwenden `KHR_materials_transmission`, der Lack `KHR_materials_clearcoat`; die Ziel-Engine muss diese Materialerweiterungen unterstützen. [Details, sieben Ansichten und Bedienachsen](docs/offroader.md).

Größere vertiefte Scheinwerfer, ein schmaler Grill mit gewölbten Lamellen und eingezogene Radlaufenden ergänzen die Außenform. Konturierte Türverkleidungen, feine Polsternähte, ausgerichtete Vordersitze und eine feste linke Fußstütze ergänzen die Kabine. Die Prüfskripte kontrollieren Haubenbewegung, Scharnieraufnahmen, ausgewählte Bedienelemente und die neue Haubenöffnung einschließlich der darunterliegenden Verstärkungen.

Eigene Oberflächenmaps unterscheiden beim Geländewagen Lack, geformten Kunststoff, Leder, Stoff, Gummi und Metall. Feine Bremsscheibenriefen, zurückhaltende Anlassfarben am Auspuff, etwas Schmutz am Unterboden und geglättete Kontaktstellen an Griffen und Trittflächen sind als Texturen beziehungsweise Vertexfarben angelegt. Beleuchtung und Reflexionen werden weiterhin vom Renderer berechnet.

Das Motorboot enthält einen geformten tiefen V-Rumpf, Deck und Cockpitboden, Bugpolster, Einzelsitze und Heckbank, einen Steuerstand mit Instrumenten sowie Reling und Beschläge. Die geteilte Windschutzscheibe verwendet ebenfalls `KHR_materials_transmission`. Der Außenborder besitzt eine Motorhaube, Unterwasserteil und profilierten Propeller. Vier Drehpunkte trennen Motortrimmung, Außenborderlenkung, Propeller und Lenkrad. Die Geometrie ist keine geprüfte Schiffskonstruktion und enthält keine Auftriebs-, Wellen- oder Antriebssimulation.

PBR-Materialien verwenden Basisfarben sowie eingebettete Metallic-/Roughness- und feine Normaltexturen. Das sind generische Mikrostrukturen, keine von einem High-Poly-Modell gebackenen Detailmaps. UVs werden automatisch pro Quellbauteil erstellt. Die nachträgliche Zusammenfassung beim Export kann UV-Überlappungen zwischen Bauteilen erzeugen; die wiederholbaren Mikrotexturen sind darauf ausgelegt. Die UVs sind keine eindeutigen Textur- oder Lightmap-Atlanten.

## Engine-Übergabe

Maßstab ist **1 Einheit = 1 Meter**. Blender: **+X vorwärts, +Y links, +Z oben**. Der GLB-Exporter wandelt standardgerecht zu glTF um: **+X vorwärts, −Z links, +Y oben**. Eine Unreal-/Unity-Importkonvertierung kann weitere Achsen-/Einheitenanpassungen übernehmen; die Maße im Manifest dienen als Referenz.

Die Dateien einzeln in den glTF-fähigen Importer der Ziel-Engine übernehmen. LOD0/1/2 müssen dort als LOD-Stufen zugewiesen werden. Kollisionskörper müssen separat zugeordnet werden; die `UCX_`-Namen allein garantieren bei glTF keine automatische Unreal-Kollision. Rad-Drehpunkte rotieren um ihr lokales Y, die Lenkung um das geneigte lokale Z, der Hauptrotor um Z und der Heckrotor um Y in der Blender-Quelldatei. Nach der Achsenkonvertierung die importierten lokalen Achsen prüfen. Die Metadaten `part_role` erleichtern die Zuordnung.

Beim Flugzeug rotiert der Propeller um sein lokales X, die drei Räder um Y, Querruder/Landeklappen/Höhenruder um ihre jeweils ausgerichtete lokale Y-Achse und das Seitenruder um seine lokale Z-Achse. Die Kollisionskörper sind statische Näherungen; Propellerbewegung und Steuerflächen benötigen eigene Engine-Einstellungen.

Der Geländewagen hat 19 Drehpunkte: vier Radachsen um lokales Y, zwei vordere Lenkachsen um lokales Z, eine Haubenscharnierachse um Y, ein geneigtes Innenraum-Lenkrad um lokales X sowie Schalthebel, Verteilergetriebehebel, Handbremse und drei Pedale um lokales Y. Dazu kommen die vier Türen und die Hecktür um lokales Z. Jedes Vorderrad ist seinem Lenk-Drehpunkt untergeordnet. Die Haube öffnet mit negativer Rotation bis −72°; die linken Türen öffnen negativ, die rechten Türen und die Hecktür positiv. Winkelgrenzen sind als Extras an den Drehpunkten gespeichert. Das Modell enthält keine Animationsclips. Eingaben, Türverriegelung, Lenkgeometrie, Federwege und Antriebsmomente müssen in der Engine eingerichtet werden. Die zehn groben Kollisionsboxen bleiben statisch und folgen auch Türen, Hecktür und Reserverad nicht. Für einen begehbaren Innenraum muss insbesondere die Kabinenkollision ersetzt werden.

Beim Motorboot ist **Z = 0 die nominale Wasserlinie** der Blender-Quelldatei; nach glTF-Konvertierung entspricht das **Y = 0**. Die tatsächliche Schwimmlage wird nicht berechnet. Der Motor klappt um lokales Y, die darunter angeordnete Lenkachse rotiert um Z und der Propeller darunter um X. Das Lenkrad dreht unabhängig um seine ausgerichtete lokale X-Achse. Die Engine muss Lenkung, Trimmung, Propeller, Kabelbewegung und Auftrieb miteinander verbinden. Die acht Kollisionskörper sind statische Näherungen und kein berechnetes Verdrängungsvolumen.

Das Ergebnis ist eine detaillierte **prozedurale Ausgangsbasis für eine Spieleproduktion**. Eine AAA-Abnahme ist damit nicht verbunden. Für ein finales Hero-Asset sind insbesondere Silhouetten-/Nahansichtsprüfung, manuell abgestimmte Retopologie und LODs, eindeutige UV-Atlanten, High-Poly-Bakes, gestaltete Abnutzung, Innenraum bei Bedarf, Rig/Animation, Fahrzeugphysik, passende Kollision und Tests in der Ziel-Engine nötig. Collider folgen in der separaten Exportdatei noch keiner Rad- oder Rotoranimation. Anzahl der Materialien, Draw Calls und Speicherbudget müssen zur konkreten Plattform passen.

Formen und Bauteile werden direkt in `build_enduro()`, `build_helicopter()`, `build_airplane()`, `build_offroader_*()` und `build_motorboat_*()` im Skript angepasst. Die Funktionen `build_offroader()` und `build_motorboat()` führen ihre jeweiligen Komponenten zusammen. `Builder.mat()` definiert Materialien und Mikrotexturen, `export_lod()` die Vereinfachung und Exportzusammenfassung.

## Prüfung

```bash
python3 tools/validate_exports.py generated
python3 -m unittest discover -s tools -p 'test_*.py' -v
blender --background --factory-startup --python-exit-code 1 --python tools/smoke_blender.py -- generated
```

Der unabhängige Validator prüft GLB-Struktur, Hierarchie, Referenzen, gültige Dreiecksindizes, endliche Geometriewerte, Normalen/UVs, eingebettete Bilder und abnehmende LOD-Dreieckszahlen. Er gibt JSON auf stdout aus und beendet sich bei Fehlern mit einem Fehlercode. Er bewertet weder Ästhetik noch Fahrzeugphysik und ersetzt keinen Engine-Test.

Der Blender-Smoke-Test importiert LOD0 und Kollisionen in frische Szenen. Er vergleicht Dateihashes, Maße und Dreieckszahlen mit dem Manifest und liest die eingebetteten Texturen. Er rotiert jeden Drehpunkt innerhalb seiner angegebenen Grenzen probeweise, prüft die Bewegung seiner Bauteile sowie die unveränderte Position unabhängiger Geometrie und setzt die Szene anschließend zurück. Beim Geländewagen prüft er außerdem verschachtelte Lenkachsen, Klarlack, transparente Optiken und per Sichtstrahl, dass die geschlossene Haube den Motor bedeckt und die geöffnete Haube ihn freigibt. Weitere Prüfungen sichern die Tiefe der Türfugen, den Sitz der Scheinwerfer und die Reifen-/Polstertexturen. Alle vier Türen müssen mit ihrer Innenverkleidung nach außen öffnen; Reserverad und Heizdrähte müssen der Hecktür folgen. Die sechs zusammenhängenden Sitzkissen-/Lehnenformen werden auf geschlossene Geometrie und erhaltene Stoff-/Lederbereiche geprüft, die Kabinenscheiben auf ihre modellierte Stärke. Beim Motorboot werden Außenborderhierarchie, Wasserlinie und Verglasung geprüft.

Die mit Blender 4.3.2 erzeugten Standardmodelle haben folgende Dreieckszahlen:

| Modell | LOD0 | LOD1 | LOD2 | Kollisionskörper |
| --- | ---: | ---: | ---: | ---: |
| Enduro | 142.576 | 70.568 | 32.030 | 4 Boxen |
| Helikopter | 85.220 | 41.216 | 17.673 | 6 Boxen |
| Flugzeug | 97.120 | 47.464 | 20.825 | 8 Boxen |
| Geländewagen | 355.573 | 172.099 | 77.134 | 10 Boxen |
| Motorboot | 72.374 | 35.564 | 16.088 | 8 Boxen |

Andere Blender-Versionen können bei automatischer Vereinfachung und UV-Erstellung abweichende Ergebnisse liefern. Die aktuellen Zahlen stehen immer in den erzeugten Manifesten.

Code und vollständig prozedural erzeugte Inhalte stehen unter der MIT-Lizenz dieses Repositorys; siehe `LICENSE`.
