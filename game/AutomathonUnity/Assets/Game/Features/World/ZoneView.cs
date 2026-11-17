using Automathon.Engine;
using UnityEngine;
using UnityEngine.Rendering;

namespace Automathon.Game
{
    /// <summary>
    /// Draws the safe zone (GameRules.ZoneDamagePerSecond) during a match: the arena outside the zone is tinted red, and
    /// the zone itself is outlined in red. Created by WorldView. The outline reuses the material of the walls' neon
    /// outlines; the red area is four sprites (one band per side, between the zone and the arena's inner faces) on the
    /// "Behind" sorting layer just above the ground, so that ships, walls and projectiles stay drawn over it.
    /// </summary>
    public class ZoneView : MonoBehaviour
    {
        private static readonly Color ZoneColor = new Color(1f, 0.2f, 0.2f, 1f);
        /// <summary>Tint of the arena outside the safe zone.</summary>
        private static readonly Color OutsideColor = new Color(1f, 0.12f, 0.12f, 0.3f);
        /// <summary>The ground quad (MudLayerController) is order 0 of the "Behind" layer.</summary>
        private const string OutsideSortingLayer = "Behind";
        private const int OutsideSortingOrder = 1;

        private LineRenderer line;
        private SpriteRenderer[] bands;  // top, bottom, left, right

        private void LateUpdate()
        {
            bool active = GameRules.Current.ZoneDamagePerSecond > 0
                          && GameplayManager.State == GameplayManager.GameplayState.Game;
            if (!active)
            {
                if (line != null)
                    line.enabled = false;
                SetBandsEnabled(false);
                return;
            }

            Vector2 half = GameplayManager.ZoneHalfSize.ToVector2();
            if (bands == null)
                CreateBands();
            UpdateBands(half, new Vector2(GameplayConstants.ARENA_HALF_WIDTH.ToWorldLength(),
                                          GameplayConstants.ARENA_HALF_HEIGHT.ToWorldLength()));

            if (line == null && !CreateLine())
                return;
            line.enabled = true;
            line.SetPosition(0, new Vector3(-half.x, -half.y, 0f));
            line.SetPosition(1, new Vector3(half.x, -half.y, 0f));
            line.SetPosition(2, new Vector3(half.x, half.y, 0f));
            line.SetPosition(3, new Vector3(-half.x, half.y, 0f));
        }

        /// <summary>The four red bands between the zone (half size `zone`) and the arena (half size `arena`).</summary>
        private void UpdateBands(Vector2 zone, Vector2 arena)
        {
            float bandY = Mathf.Max(0f, arena.y - zone.y);
            float bandX = Mathf.Max(0f, arena.x - zone.x);
            PlaceBand(bands[0], new Vector2(0f, (zone.y + arena.y) / 2f), new Vector2(2f * arena.x, bandY));
            PlaceBand(bands[1], new Vector2(0f, -(zone.y + arena.y) / 2f), new Vector2(2f * arena.x, bandY));
            PlaceBand(bands[2], new Vector2(-(zone.x + arena.x) / 2f, 0f), new Vector2(bandX, 2f * zone.y));
            PlaceBand(bands[3], new Vector2((zone.x + arena.x) / 2f, 0f), new Vector2(bandX, 2f * zone.y));
        }

        private static void PlaceBand(SpriteRenderer band, Vector2 center, Vector2 size)
        {
            bool visible = size.x > 0.001f && size.y > 0.001f;
            band.enabled = visible;
            if (!visible)
                return;
            band.transform.position = new Vector3(center.x, center.y, 0f);
            band.transform.localScale = new Vector3(size.x, size.y, 1f);
        }

        private void CreateBands()
        {
            // A 1 x 1 unit white square, tinted by the renderer's colour
            Texture2D white = Texture2D.whiteTexture;
            Sprite square = Sprite.Create(white, new Rect(0, 0, white.width, white.height), new Vector2(0.5f, 0.5f),
                                          white.width);
            bands = new SpriteRenderer[4];
            for (int i = 0; i < bands.Length; i++)
            {
                SpriteRenderer band = new GameObject("SafeZoneOutside").AddComponent<SpriteRenderer>();
                band.transform.SetParent(transform, false);
                band.sprite = square;
                band.color = OutsideColor;
                band.sortingLayerName = OutsideSortingLayer;
                band.sortingOrder = OutsideSortingOrder;
                band.shadowCastingMode = ShadowCastingMode.Off;
                band.enabled = false;
                bands[i] = band;
            }
        }

        private void SetBandsEnabled(bool enabled)
        {
            if (bands == null)
                return;
            foreach (SpriteRenderer band in bands)
                band.enabled = enabled;
        }

        private bool CreateLine()
        {
            LineRenderer template = null;
            foreach (LineRenderer candidate in FindObjectsByType<LineRenderer>(FindObjectsSortMode.None))
            {
                if (candidate.sharedMaterial != null)
                {
                    template = candidate;
                    break;
                }
            }
            Material material = template != null ? template.sharedMaterial : FallbackMaterial();
            if (material == null)
                return false;  // walls not spawned yet: try again next frame

            line = new GameObject("SafeZoneOutline").AddComponent<LineRenderer>();
            line.transform.SetParent(transform, false);
            line.useWorldSpace = true;
            line.loop = true;
            line.positionCount = 4;
            line.widthMultiplier = template != null ? template.widthMultiplier * 1.5f : 0.1f;
            line.numCornerVertices = 0;
            line.numCapVertices = 0;
            line.alignment = LineAlignment.View;
            line.textureMode = LineTextureMode.Stretch;
            line.sharedMaterial = material;
            line.startColor = ZoneColor;
            line.endColor = ZoneColor;
            line.sortingOrder = (template != null ? template.sortingOrder : 1) + 1;
            line.shadowCastingMode = ShadowCastingMode.Off;
            return true;
        }

        private static Material FallbackMaterial()
        {
            Shader shader = Shader.Find("Sprites/Default");
            return shader != null ? new Material(shader) : null;
        }
    }
}
