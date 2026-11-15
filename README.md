# Space Eddus

Health monitoring software for astronauts on long-duration missions, built for the **NASA Space Apps Challenge 2026** ("Create Health Monitoring Software for Astronauts on Space Missions"). Repository: `Space-Oddity`.

## Summary

Astronauts far from Earth have to spot health changes in themselves. Space Eddus gathers daily health indicators, compares each astronaut against their own baseline, and turns the result into a traffic-light status, a 0–100 readiness score and alerts with an action plan.

- **Scenario:** low Earth orbit (ISS) or cislunar mission, 4 fictional astronauts over 50 days.
- **Indicators:** heart rate, HRV, SpO₂, skin temperature, respiratory rate, estimated blood pressure and sleep (watch); glucose; salivary cortisol, DHEA and alpha-amylase; body composition (bioimpedance chair); hemoglobin; menstrual cycle; daily radiation dose.
- **Clinical problems watched:** muscle loss and malnutrition, circadian and sleep disruption, early infection, plus safety checks for glucose, blood pressure, SpO₂ and radiation.
- **Real data where it exists:** solar particle events from NASA DONKI and proton flux from NOAA SWPC. The other signals are generated from the published clinical ranges listed in the references.
- **Roles:**
  - The flight surgeon sees the whole crew, reviews each astronaut, assigns pills, meals and notifications on chosen weekdays, and can launch simulated emergencies.
  - Astronauts see their own status, diet, medication and notifications, and mark notifications as seen.
- **Alert policy:** red alerts are immediate, yellow alerts go into the daily report, and simulated scenarios are kept apart from the mission data and can be reverted.

## Run it locally

Requires Python 3.11+ and Git. Run every command from the repo root.

```bash
python -m pip install -r requirements.txt
python -m scripts.generar_datos        # demo mission (syncs NASA DONKI / NOAA)
python -m scripts.procesar_mision      # daily reports, scores and alerts
python -m streamlit run app.py         # opens http://localhost:8501
python -m pytest                       # tests
```

Demo accounts (password `SpaceOddity2026`):

| Role | Email |
| --- | --- |
| Flight surgeon | laura.jimenez@spaceoddity.dev |
| Astronaut | andrea.solis@spaceoddity.dev |
| Astronaut | mateo.rojas@spaceoddity.dev |
| Astronaut | valeria.mora@spaceoddity.dev |
| Astronaut | daniel.vargas@spaceoddity.dev |

`.streamlit/secrets.toml` holds the NASA API key and is never committed. Copy it from `.streamlit/secrets.toml.example`.

## Deploy on Streamlit Community Cloud

1. Generate the database and commit it (the app reads `data/astro.db`):
   ```bash
   python -m scripts.generar_datos
   python -m scripts.procesar_mision
   python -c "import sqlite3; c=sqlite3.connect('data/astro.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
   git add data/astro.db
   git commit -m "chore(data): demo database for deploy"
   git push origin main
   ```
2. Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub and choose **Create app**.
3. Select this repository, branch `main` and main file `app.py`.
4. In **Advanced settings**, choose Python 3.12 and paste the content of your `secrets.toml` in **Secrets**.
5. Click **Deploy**. Every `git push` to `main` updates the app.

Changes made in the cloud, such as new assignments or seen notifications, are lost when the app restarts or redeploys. The app then goes back to the committed database.

## Project structure

| Folder | Content |
| --- | --- |
| `pages/` | One screen of the Figma design per file |
| `components/` | Shared UI: menu, patient table, forms, widgets, charts |
| `services/` | Database, login, doctor actions, reminders, simulator, NASA/NOAA clients |
| `core/` | Clinical ranges, equations, alerts, score, mission calendar (pure Python) |
| `scripts/` | Database schema and mission generator |
| `assets/`, `static/`, `.streamlit/` | Styles, images, icons and theme |
| `tests/` | pytest suite, including UI tests |

## Team

In alphabetical order by last name:

- Máyerlin Dilana Gamboa González
- Sett Jeanluigi Hernández Cambronero
- Naomy Sofía Mora Brenes
- Alejandra María Solís Ocampo

## References

**Data and APIs**

- NASA Human Research Program. [Five hazards of human spaceflight](https://www.nasa.gov/hrp/hazards/).
- NASA CCMC. [DONKI: Space Weather Database Of Notifications, Knowledge, Information](https://ccmc.gsfc.nasa.gov/tools/DONKI).
- NOAA SWPC. [GOES proton flux](https://www.swpc.noaa.gov/products/goes-proton-flux).

**Clinical ranges and equations**

1. Cederholm T, et al. GLIM criteria for the diagnosis of malnutrition. Clin Nutr. 2019;38(1):1–9. https://doi.org/10.1016/j.clnu.2018.08.002
2. Janssen I, et al. Skeletal muscle cutpoints associated with elevated physical disability risk. Am J Epidemiol. 2004;159(4):413–421. https://doi.org/10.1093/aje/kwh058
3. Cruz-Jentoft AJ, et al. Sarcopenia: revised European consensus (EWGSOP2). Age Ageing. 2019;48(1):16–31. https://doi.org/10.1093/ageing/afy169
4. Japan Society for the Study of Obesity. New criteria for 'obesity disease' in Japan. Circ J. 2002;66:987–992. https://doi.org/10.1253/circj.66.987
5. Tanita Europe. How is visceral fat measured by Tanita? https://support.tanita.eu/support/solutions/articles/60000677375-how-is-visceral-fat-measured-by-tanita-
6. Canadian Space Agency. What happens to muscles in space? https://asc-csa.gc.ca/eng/sciences/osm/muscles.asp
7. Mifflin MD, et al. A new predictive equation for resting energy expenditure. Am J Clin Nutr. 1990;51(2):241–247. https://doi.org/10.1093/ajcn/51.2.241
8. Barger LK, et al. Prevalence of sleep deficiency and use of hypnotic drugs in astronauts. Lancet Neurol. 2014;13(9):904–912. https://doi.org/10.1016/S1474-4422(14)70122-X
9. Watson NF, et al. Recommended amount of sleep for a healthy adult (AASM/SRS). Sleep. 2015;38(6):843–844. https://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/
10. Wüst S, et al. The cortisol awakening response: normal values and confounds. Noise Health. 2000;2(7):79–88. https://pubmed.ncbi.nlm.nih.gov/12689474/
11. Pruessner JC, et al. Two formulas for computation of the area under the curve. Psychoneuroendocrinology. 2003;28(7):916–931. https://doi.org/10.1016/S0306-4530(02)00108-7
12. Diurnal salivary cortisol reference values (Medicina, 2021, table 2). https://pmc.ncbi.nlm.nih.gov/articles/PMC8537520/
13. Salimetrics. Salivary alpha-amylase: example ranges. https://salimetrics.com/analyte/salivary-alpha-amylase
14. Nater UM, Rohleder N. Salivary alpha-amylase as a biomarker for the sympathetic nervous system. Psychoneuroendocrinology. 2009;34(4):486–496. https://doi.org/10.1016/j.psyneuen.2009.01.014
15. Task Force of the ESC and NASPE. Heart rate variability: standards of measurement. Circulation. 1996;93(5):1043–1065. https://doi.org/10.1161/01.CIR.93.5.1043
16. Nunan D, et al. Normal values for short-term heart rate variability in healthy adults. PACE. 2010;33(11):1407–1417. https://doi.org/10.1111/j.1540-8159.2010.02841.x
17. Bone RC, et al. ACCP/SCCM Consensus Conference: definitions for sepsis and organ failure. Chest. 1992;101(6):1644–1655. https://doi.org/10.1378/chest.101.6.1644
18. Mishra T, et al. Pre-symptomatic detection of COVID-19 from smartwatch data. Nat Biomed Eng. 2020;4:1208–1220. https://www.nature.com/articles/s41551-020-00640-6
19. Oura. Body temperature: deviation from personal baseline. https://support.ouraring.com/hc/en-us/articles/360025587493
20. Karjalainen J, Viitasalo M. Fever and cardiac rhythm. Arch Intern Med. 1986;146(6):1169–1171. https://pubmed.ncbi.nlm.nih.gov/2424378/
21. Agiostratidou G, et al. Standardizing clinically meaningful outcome measures beyond HbA1c. Diabetes Care. 2017;40:1622–1630. https://doi.org/10.2337/dc17-1624
22. Battelino T, et al. International consensus on time in range. Diabetes Care. 2019;42(8):1593–1603. https://pmc.ncbi.nlm.nih.gov/articles/PMC6973648
23. Whelton PK, et al. 2017 ACC/AHA guideline for high blood pressure in adults. https://acc.org/Latest-in-Cardiology/Articles/2017/11/08/11/47/mon-5pm-bp-guideline-aha-2017
24. Zhang S, et al. First measurements of the radiation dose on the lunar surface. Sci Adv. 2020;6(39):eaaz1334. https://doi.org/10.1126/sciadv.aaz1334
25. Hassler DM, et al. Mars' surface radiation environment measured with Curiosity. Science. 2014;343(6169). https://doi.org/10.1126/science.1244797
26. NASA OCHMO. Radiation protection technical brief (NASA-STD-3001). https://www.nasa.gov/wp-content/uploads/2023/03/radiation-protection-technical-brief-ochmo.pdf
27. NOAA SWPC. GOES proton flux. https://www.swpc.noaa.gov/products/goes-proton-flux
28. Tanaka H, Monahan KD, Seals DR. Age-predicted maximal heart rate revisited. J Am Coll Cardiol. 2001;37(1):153–156. https://pubmed.ncbi.nlm.nih.gov/11153730/

Thresholds marked as team decisions in the code are design choices, not published values.

## Declared use of AI

- **Code:** the team used an AI assistant (Claude, by Anthropic) to write, refactor and test part of the code, and to translate interface text. The team reviewed every change and kept it only after the automated tests passed.
- **Documentation:** the same assistant helped draft this README and the team guide.
- **Video:** the presentation video includes an AI-generated clip that illustrates the monitoring device. The clip is a concept, not a real product.
- **Team decisions:** the problem framing, the clinical ranges and their sources, the Figma design and every design decision are the team's own work.
- **Not AI-generated:** all astronaut data is synthetic and created by the project's generator scripts. No real patient data is used.
