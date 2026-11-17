using Automathon.Engine;
using Automathon.Engine.Physics;
using System;
using System.Collections.Generic;

namespace Automathon.Game.MapSystem
{
    /// <summary>
    /// Génère une arène de "murs de couverture" : panneaux fins orientés aléatoirement,
    /// placés par échantillonnage Poisson-disk, en symétrie centrale (équité 1v1 : chaque
    /// mur a son image en -p) et avec connectivité A↔B garantie (re-tirage déterministe).
    ///
    /// Déterminisme : le PRNG (mulberry32) et le Poisson-disk sont portés bit-à-bit depuis
    /// l'outil d'aperçu, donc une même seed => même carte, sur n'importe quelle plateforme.
    /// Seul le test de connectivité utilise de la trigo flottante (cos/sin) ; il ne sert qu'à
    /// accepter/re-tirer, pas à la physique. Pour du bit-exact cross-plateforme absolu,
    /// remplace-le par du fixed-point.
    /// </summary>
    public class RandomMapGenerator
    {
        // ---------- Paramètres carte ----------
        private const int MapLength = 27500;
        private const int MapHeight = 15500;
        private const int HX = MapLength / 2;   // 13750
        private const int HY = MapHeight / 2;   // 7750

        // Murs invisibles de bord, face intérieure en ±13700 / ±7700 (Python : bridge.datatypes.ARENA_HALF_WIDTH /
        // ARENA_HALF_HEIGHT, à garder à jour). Deux jeux de murs, de même face intérieure :
        // - les murs fins d'origine (100 d'épaisseur, centrés en ±HX / ±HY, couche InvisibleWall) arrêtent les missiles
        //   et les boucliers, comme avant ;
        // - les murs épais (couche ArenaBorder) n'arrêtent que les vaisseaux. Un vaisseau coincé contre le bord (par un
        //   mur de la carte, par exemple) peut s'enfoncer dans le bord ; quand son centre entrait dans un mur fin, le
        //   moteur le repoussait vers l'extérieur ou le long du mur, et il sortait de l'arène.
        private const int BorderThickness = 100;
        private const int BorderInnerX = HX - BorderThickness / 2, BorderInnerY = HY - BorderThickness / 2;
        private const int ThickBorder = 2000;

        // Generation works in thousandths of a unit (1000 = 1 world unit), the output is converted to Fixed.
        // Points d'apparition des vaisseaux (GameplayManager.Reset) : zone dégagée et test de connexité
        private const int SpawnAX = -10000, SpawnAY = 0;
        private const int SpawnBX = 10000, SpawnBY = 0;

        // ---------- Paramètres génération ----------
        private const int SpawnClear = 1500;       // aucun mur à moins de cette distance du point d'apparition (surface du mur)
        private const int WallLenMin = 1900, WallLenMax = 4500;
        private const int WallThickMin = 240, WallThickMax = 380;
        private const double BaseMinDist = 2600.0; // distance min Poisson à density=1
        private const int MaxAttempts = 16;        // re-tirages si non connexe

        /// <summary>Densité de murs par défaut.</summary>
        public const double DefaultDensity = 0.7;

        private Rng rng;

        private struct WallSpec
        {
            public double X, Y, Len, Thick, Angle; // Angle en radians
        }

        // ======================= Points d'entrée =======================

        /// <summary>Génère une carte avec une seed aléatoire.</summary>
        public static void GenerateRandomMap()
            => GenerateRandomMap(Environment.TickCount);

        /// <summary>Génère une carte reproductible à partir d'une seed (et densité optionnelle, 0 = aucun mur).</summary>
        public static void GenerateRandomMap(int seed, double density = DefaultDensity)
        {
            RandomMapGenerator gen = new RandomMapGenerator();
            List<Entity> walls = gen.Build(seed, density);
            MapGenerator.InstantiateMap(new Map("random_map", walls));
        }

        // ======================= Pipeline =======================

        private List<Entity> Build(int seed, double density)
        {
            List<WallSpec> specs = null;
            for (int attempt = 0; attempt < MaxAttempts; attempt++)
            {
                // attempt 0 => seed exacte (parité avec l'aperçu) ; sinon décalage déterministe
                int s = unchecked(seed + attempt * (int)0x9E3779B9);
                specs = GenerateSpecs(s, density);
                if (IsConnected(specs))
                    break;
            }
            return Assemble(specs);
        }

        private List<WallSpec> GenerateSpecs(int seed, double density)
        {
            if (density <= 0)
                return new List<WallSpec>(); // arène vide (seuls les murs invisibles de bord sont ajoutés)

            rng = new Rng(seed);
            double minDist = BaseMinDist / density;

            List<(double x, double y)> pts =
                Poisson(-HX + 1200, -HY + 1200, -1400, HY - 1200, minDist);

            List<WallSpec> specs = new List<WallSpec>();
            foreach (var p in pts)
            {
                double len = rng.Range(WallLenMin, WallLenMax);
                double thick = rng.Range(WallThickMin, WallThickMax);
                double angle = rng.Range(0, Math.PI);
                WallSpec spec = new WallSpec { X = p.x, Y = p.y, Len = len, Thick = thick, Angle = angle };
                // dégagement du point d'apparition, sur la surface du mur (un mur de 4,5 de long centré à 2 unités du
                // point le recouvrait). Le mur symétrique est à la même distance de l'autre point d'apparition.
                if (!NearSpawn(spec))
                    specs.Add(spec);
            }

            // symétrie centrale : chaque mur + son image en -p (même taille, même angle)
            int n = specs.Count;
            for (int i = 0; i < n; i++)
            {
                WallSpec b = specs[i];
                specs.Add(new WallSpec { X = -b.X, Y = -b.Y, Len = b.Len, Thick = b.Thick, Angle = b.Angle });
            }
            return specs;
        }

        /// <summary>
        /// Is the wall closer than SpawnClear to the first spawn point? Computed on the wall as Assemble builds it
        /// (thousandths rounded, rotation in Fixed) with fixed-point trigonometry: the same on every platform.
        /// </summary>
        private static bool NearSpawn(WallSpec s)
        {
            Fixed rotation = Fixed.FromMilli(RoundJS(s.Angle * 1000.0));
            Fixed dx = Fixed.FromMilli(SpawnAX - RoundJS(s.X)), dy = Fixed.FromMilli(SpawnAY - RoundJS(s.Y));
            Fixed c = FixedMath.Cos(rotation), sn = FixedMath.Sin(rotation);
            // spawn point in the wall's frame, then its distance to the rectangle
            Fixed lx = (dx * c + dy * sn).Abs(), ly = (dy * c - dx * sn).Abs();
            Fixed ex = lx - Fixed.FromMilli(RoundJS(s.Len)) / 2, ey = ly - Fixed.FromMilli(RoundJS(s.Thick)) / 2;
            if (ex < 0) ex = Fixed.Zero;
            if (ey < 0) ey = Fixed.Zero;
            Fixed clear = Fixed.FromMilli(SpawnClear);
            return ex * ex + ey * ey < clear * clear;
        }

        private List<Entity> Assemble(List<WallSpec> specs)
        {
            List<Entity> list = new List<Entity>();

            foreach (WallSpec s in specs)
            {
                // rounded to integer thousandths first (parity with the preview tool), then converted exactly to Fixed
                FixedVector2 pos = Milli(RoundJS(s.X), RoundJS(s.Y));
                FixedVector2 size = Milli(RoundJS(s.Len), RoundJS(s.Thick));

                Fixed rotation = Fixed.FromMilli(RoundJS(s.Angle * 1000.0));   // [0, π] rad
                list.Add(new Wall(pos, size, rotation));
            }

            // murs de bord épais, pour les vaisseaux seulement (ils se recouvrent dans les coins). Ajoutés à la place
            // des murs fins dans la liste, pour que l'ordre de résolution des contacts des vaisseaux reste le même.
            const int cx = BorderInnerX + ThickBorder / 2, cy = BorderInnerY + ThickBorder / 2;
            const int fullX = 2 * (BorderInnerX + ThickBorder), fullY = 2 * (BorderInnerY + ThickBorder);
            list.Add(new InvisibleWall(Milli(0, cy), Milli(fullX, ThickBorder), 0, CollisionLayer.ArenaBorder));
            list.Add(new InvisibleWall(Milli(0, -cy), Milli(fullX, ThickBorder), 0, CollisionLayer.ArenaBorder));
            list.Add(new InvisibleWall(Milli(-cx, 0), Milli(ThickBorder, fullY), 0, CollisionLayer.ArenaBorder));
            list.Add(new InvisibleWall(Milli(cx, 0), Milli(ThickBorder, fullY), 0, CollisionLayer.ArenaBorder));

            // murs de bord fins d'origine, pour les missiles et les boucliers
            list.Add(new InvisibleWall(Milli(0, HY), Milli(MapLength, BorderThickness), 0));
            list.Add(new InvisibleWall(Milli(0, -HY), Milli(MapLength, BorderThickness), 0));
            list.Add(new InvisibleWall(Milli(-HX, 0), Milli(BorderThickness, MapHeight), 0));
            list.Add(new InvisibleWall(Milli(HX, 0), Milli(BorderThickness, MapHeight), 0));

            return list;
        }

        // ======================= Connectivité (grille grossière) =======================

        private bool IsConnected(List<WallSpec> specs)
        {
            int gw = 120;
            int gh = RoundJS(120.0 * MapHeight / MapLength); // 67
            double cw = (double)MapLength / gw, ch = (double)MapHeight / gh;

            // A cell is free when no wall covers its center. Each wall only tests the cells around it (its rotated
            // rectangle's bounding box, widened), with the same exact test as before: the result is unchanged, but
            // the cost no longer grows with cells x walls (and cos/sin are computed once per wall).
            bool[] free = new bool[gw * gh];
            for (int k = 0; k < free.Length; k++)
                free[k] = true;
            foreach (WallSpec s in specs)
            {
                double c = Math.Cos(-s.Angle), sn = Math.Sin(-s.Angle);
                double halfLen = s.Len / 2, halfThick = s.Thick / 2;
                // Half extents of the rotated rectangle, plus a margin far above floating point rounding
                double ex = Math.Abs(c) * halfLen + Math.Abs(sn) * halfThick + 1.0;
                double ey = Math.Abs(sn) * halfLen + Math.Abs(c) * halfThick + 1.0;
                int i0 = Clamp((int)Math.Floor((s.X - ex + HX) / cw - 0.5) - 1, 0, gw - 1);
                int i1 = Clamp((int)Math.Ceiling((s.X + ex + HX) / cw - 0.5) + 1, 0, gw - 1);
                int j0 = Clamp((int)Math.Floor((s.Y - ey + HY) / ch - 0.5) - 1, 0, gh - 1);
                int j1 = Clamp((int)Math.Ceiling((s.Y + ey + HY) / ch - 0.5) + 1, 0, gh - 1);
                for (int j = j0; j <= j1; j++)
                    for (int i = i0; i <= i1; i++)
                    {
                        if (!free[j * gw + i])
                            continue;
                        double wx = -HX + (i + 0.5) * cw;
                        double wy = -HY + (j + 0.5) * ch;
                        if (Covers(wx, wy, s, c, sn))
                            free[j * gw + i] = false;
                    }
            }

            int Cell(int x, int y, out int cy)
            {
                int cx = Clamp((int)((x + HX) / cw), 0, gw - 1);
                cy = Clamp((int)((y + HY) / ch), 0, gh - 1);
                return cx;
            }

            int ax = Cell(SpawnAX, SpawnAY, out int ay);
            int bx = Cell(SpawnBX, SpawnBY, out int by);
            if (!free[ay * gw + ax] || !free[by * gw + bx]) return false;

            bool[] seen = new bool[gw * gh];
            Stack<int> st = new Stack<int>();
            st.Push(ay * gw + ax); seen[ay * gw + ax] = true;
            int[] dx4 = { 1, -1, 0, 0 }, dy4 = { 0, 0, 1, -1 };
            while (st.Count > 0)
            {
                int cur = st.Pop();
                int x = cur % gw, y = cur / gw;
                if (x == bx && y == by) return true;
                for (int k = 0; k < 4; k++)
                {
                    int nx = x + dx4[k], ny = y + dy4[k];
                    if (nx < 0 || ny < 0 || nx >= gw || ny >= gh) continue;
                    int ni = ny * gw + nx;
                    if (free[ni] && !seen[ni]) { seen[ni] = true; st.Push(ni); }
                }
            }
            return false;
        }

        /// <summary>Does wall s cover the point (wx, wy)? c, sn: cos and sin of -s.Angle.</summary>
        private static bool Covers(double wx, double wy, WallSpec s, double c, double sn)
        {
            double dx = wx - s.X, dy = wy - s.Y;
            double lx = dx * c - dy * sn;
            double ly = dx * sn + dy * c;
            return Math.Abs(lx) <= s.Len / 2 && Math.Abs(ly) <= s.Thick / 2;
        }

        // ======================= Poisson-disk =======================

        private List<(double x, double y)> Poisson(double x0, double y0, double x1, double y1, double r, int k = 18)
        {
            double cell = r / Math.Sqrt(2);
            double W = x1 - x0, H = y1 - y0;
            int gw = Math.Max(1, (int)Math.Ceiling(W / cell));
            int gh = Math.Max(1, (int)Math.Ceiling(H / cell));
            int[] grid = new int[gw * gh];
            for (int i = 0; i < grid.Length; i++) grid[i] = -1;

            List<(double x, double y)> pts = new List<(double, double)>();
            List<int> active = new List<int>();

            (int, int) Gxy(double px, double py)
            {
                int cx = Math.Min(gw - 1, (int)((px - x0) / cell));
                int cy = Math.Min(gh - 1, (int)((py - y0) / cell));
                return (cx, cy);
            }
            bool Fits(double px, double py)
            {
                var (cx, cy) = Gxy(px, py);
                for (int iy = Math.Max(0, cy - 2); iy <= Math.Min(gh - 1, cy + 2); iy++)
                    for (int ix = Math.Max(0, cx - 2); ix <= Math.Min(gw - 1, cx + 2); ix++)
                    {
                        int id = grid[iy * gw + ix];
                        if (id >= 0)
                        {
                            double qx = pts[id].x, qy = pts[id].y;
                            if ((qx - px) * (qx - px) + (qy - py) * (qy - py) < r * r) return false;
                        }
                    }
                return true;
            }

            double fx = x0 + rng.NextDouble() * W, fy = y0 + rng.NextDouble() * H;
            pts.Add((fx, fy)); active.Add(0);
            var (g0x, g0y) = Gxy(fx, fy); grid[g0y * gw + g0x] = 0;

            while (active.Count > 0)
            {
                int ai = (int)(rng.NextDouble() * active.Count);
                var p = pts[active[ai]];
                bool placed = false;
                for (int t = 0; t < k; t++)
                {
                    double ang = rng.NextDouble() * Math.PI * 2;
                    double rad = r * (1 + rng.NextDouble());
                    double nx = p.x + Math.Cos(ang) * rad;
                    double ny = p.y + Math.Sin(ang) * rad;
                    if (nx < x0 || nx > x1 || ny < y0 || ny > y1) continue;
                    if (Fits(nx, ny))
                    {
                        int id = pts.Count;
                        pts.Add((nx, ny)); active.Add(id);
                        var (gx, gy) = Gxy(nx, ny); grid[gy * gw + gx] = id;
                        placed = true; break;
                    }
                }
                if (!placed) active.RemoveAt(ai);
            }
            return pts;
        }

        // ======================= Utilitaires =======================

        private static int RoundJS(double v) => (int)Math.Floor(v + 0.5);
        private static FixedVector2 Milli(int x, int y) => new FixedVector2(Fixed.FromMilli(x), Fixed.FromMilli(y));
        private static int Clamp(int v, int lo, int hi) => v < lo ? lo : (v > hi ? hi : v);

        /// <summary>
        /// PRNG déterministe. Même seed => même séquence,
        /// indépendamment de la plateforme (contrairement à System.Random).
        /// </summary>
        private sealed class Rng
        {
            private uint state;
            public Rng(int seed) { state = (uint)seed; }

            public double NextDouble()
            {
                unchecked
                {
                    state += 0x6D2B79F5u;
                    uint t = state;
                    t = (t ^ (t >> 15)) * (1u | state);
                    t = (t + ((t ^ (t >> 7)) * (61u | t))) ^ t;
                    return (t ^ (t >> 14)) / 4294967296.0;
                }
            }

            public double Range(double a, double b) => a + (b - a) * NextDouble();
        }

    }
}