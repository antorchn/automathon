using Assets.Game.View;
using UnityEngine;

public class CameraShaker : MonoBehaviour
{
    public void CameraShake(float shakeTime, float shakeIntensity, bool unscaledTime = false)
    {
        StartCoroutine(Shaker.Shake(transform, shakeTime, shakeIntensity, unscaledTime));
    }
    public void CameraTranslate(Vector2 dir, int miliTime, float distance)
    {
        StartCoroutine(Shaker.Translate(transform, dir, miliTime, distance));
    }
}
