using System.Collections;
using UnityEngine;

namespace Assets.Game.View
{
    public static class Shaker
    {
        /// Only ever moves the object relatively to where it is, like <see cref="Translate"/>, so any number of
        /// shakes and translations can overlap: each one removes exactly what it added when it ends.
        /// <param name="unscaledTime">Lasts real seconds, whatever Time.timeScale is (the end-of-game slow motion would stretch it)</param>
        public static IEnumerator Shake(Transform entityView, float time, float intensity, bool unscaledTime = false)
        {
            Vector2 localShakePos = Vector2.zero;

            float t = time;
            while (t >= 0)
            {
                Vector2 random = new Vector2(Random.Range(-1f, 1f), Random.Range(-1f, 1f)) * intensity;

                float maxBound = intensity * t / time;

                random = new Vector2(Mathf.Clamp(localShakePos.x + random.x, -maxBound, +maxBound), Mathf.Clamp(localShakePos.y + random.y, -maxBound, +maxBound)) - localShakePos;

                entityView.localPosition += new Vector3(random.x, random.y, 0);

                localShakePos += random;

                t -= unscaledTime ? Time.unscaledDeltaTime : Time.deltaTime;
                yield return 0;
            }

            entityView.localPosition -= new Vector3(localShakePos.x, localShakePos.y);
        }

        /// Never saves and restores an absolute position: that would bake any effect in progress into the object's
        /// position when it starts, and erase the others' movement every frame.
        public static IEnumerator Translate(Transform entityView, Vector2 dir, int miliTime, float distance)
        {
            Vector2 direction = dir;

            Vector3 appliedOffset = Vector3.zero;

            float time = (float)miliTime / 1000f;

            float V = 4 * distance / time;
            float g = V / time * 2;

            float t = time;
            while (t >= 0)
            {
                float deltaDist = -g * t * t / 2 + V * t;

                Vector3 offset = deltaDist * direction;
                entityView.localPosition += offset - appliedOffset;
                appliedOffset = offset;

                t -= Time.deltaTime;
                yield return 0;
            }

            entityView.localPosition -= appliedOffset;
        }
    }
}
