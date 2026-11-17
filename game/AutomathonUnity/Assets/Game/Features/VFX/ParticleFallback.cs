using UnityEngine;

namespace Automathon.Game
{
    /// <summary>
    /// Explosions drawn with a ParticleSystem where the Visual Effect Graph can't run: it needs compute shaders, which
    /// WebGL2 (the browser build) doesn't have. Built by code, so the prefabs don't change and the desktop builds keep
    /// their VFX Graph effects. In the editor, menu Automathon > Particle fallback (WebGL look) to preview them.
    /// </summary>
    public static class ParticleFallback
    {
#if UNITY_EDITOR
        public const string EditorPrefKey = "Automathon.ParticleFallback";
        public static bool Active => !SystemInfo.supportsComputeShaders || UnityEditor.EditorPrefs.GetBool(EditorPrefKey, false);
#else
        public static bool Active => !SystemInfo.supportsComputeShaders;
#endif

        private static Material material;
        private static int sortingLayerId;
        private static int sortingOrder;

        /// <summary>Particles use the material and the sorting of this renderer (the tank's dash particles: additive,
        /// default particle texture).</summary>
        public static void UseLookOf(ParticleSystemRenderer reference)
        {
            if (material != null || reference == null || reference.sharedMaterial == null)
                return;
            material = reference.sharedMaterial;
            sortingLayerId = reference.sortingLayerID;
            sortingOrder = reference.sortingOrder + 1;
        }

        /// <summary>Muzzle flash of the machine gun, towards direction, carried along with the tank's velocity.</summary>
        public static void MiniExplosion(Vector3 position, Vector2 direction, Vector2 tankVelocity)
        {
            const float arc = 70f;
            ParticleSystem ps = Create("MiniExplosion (particles)", position, 0.3f);
            var main = ps.main;
            main.startLifetime = new ParticleSystem.MinMaxCurve(0.08f, 0.18f);
            main.startSpeed = new ParticleSystem.MinMaxCurve(3f, 7f);
            main.startSize = new ParticleSystem.MinMaxCurve(0.12f, 0.28f);
            main.startColor = new ParticleSystem.MinMaxGradient(new Color(1f, 0.95f, 0.6f), new Color(1f, 0.55f, 0.1f));
            Burst(ps, 10);

            var shape = ps.shape;
            shape.enabled = true;
            shape.shapeType = ParticleSystemShapeType.Circle;
            shape.radius = 0.05f;
            shape.arc = arc;
            float angle = Mathf.Atan2(direction.y, direction.x) * Mathf.Rad2Deg;
            shape.rotation = new Vector3(0f, 0f, angle - arc / 2f);

            var velocity = ps.velocityOverLifetime;
            velocity.enabled = true;
            velocity.space = ParticleSystemSimulationSpace.World;
            velocity.x = new ParticleSystem.MinMaxCurve(tankVelocity.x);
            velocity.y = new ParticleSystem.MinMaxCurve(tankVelocity.y);
            velocity.z = new ParticleSystem.MinMaxCurve(0f);

            FadeAndShrink(ps, drag: 6f);
            ps.Play();
        }

        /// <summary>Missile explosion filling its area of effect (radius in world units).</summary>
        public static void BigExplosion(Vector3 position, float radius)
        {
            // Fire, reaching about the radius of the area of effect
            ParticleSystem fire = Create("BigExplosion (particles)", position, 1.5f);
            var main = fire.main;
            main.startLifetime = new ParticleSystem.MinMaxCurve(0.3f, 0.6f);
            main.startSpeed = new ParticleSystem.MinMaxCurve(radius * 2f, radius * 5f);
            main.startSize = new ParticleSystem.MinMaxCurve(radius * 0.25f, radius * 0.6f);
            main.startColor = new ParticleSystem.MinMaxGradient(new Color(1f, 0.85f, 0.4f), new Color(1f, 0.35f, 0.05f));
            Burst(fire, 60);
            CircleShape(fire, radius * 0.15f);
            FadeAndShrink(fire, drag: 5f);
            fire.Play();

            // Flash
            ParticleSystem flash = Create("BigExplosion flash", position, 0.5f);
            main = flash.main;
            main.startLifetime = 0.12f;
            main.startSpeed = 0f;
            main.startSize = radius * 2.2f;
            main.startColor = new Color(1f, 0.9f, 0.7f, 0.8f);
            Burst(flash, 1);
            FadeAndShrink(flash, drag: 0f, shrink: false);
            flash.Play();

            // Smoke, slower and longer
            ParticleSystem smoke = Create("BigExplosion smoke", position, 2f);
            main = smoke.main;
            main.startLifetime = new ParticleSystem.MinMaxCurve(0.7f, 1.2f);
            main.startSpeed = new ParticleSystem.MinMaxCurve(radius * 0.5f, radius * 1.5f);
            main.startSize = new ParticleSystem.MinMaxCurve(radius * 0.4f, radius * 0.8f);
            main.startColor = new ParticleSystem.MinMaxGradient(new Color(0.45f, 0.3f, 0.25f, 0.35f), new Color(0.25f, 0.2f, 0.2f, 0.25f));
            Burst(smoke, 18);
            CircleShape(smoke, radius * 0.3f);
            FadeAndShrink(smoke, drag: 2f, shrink: false);
            smoke.Play();
        }

        private static ParticleSystem Create(string name, Vector3 position, float duration)
        {
            var go = new GameObject(name);
            go.transform.position = position;
            ParticleSystem ps = go.AddComponent<ParticleSystem>();
            ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear); // configured before it plays

            var main = ps.main;
            main.duration = duration;
            main.loop = false;
            main.playOnAwake = false;
            main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.gravityModifier = 0f;
            main.maxParticles = 200;
            main.stopAction = ParticleSystemStopAction.Destroy; // the GameObject goes away once the effect is over

            var emission = ps.emission;
            emission.enabled = true;
            emission.rateOverTime = 0f;

            var shape = ps.shape;
            shape.enabled = false;

            var renderer = go.GetComponent<ParticleSystemRenderer>();
            renderer.renderMode = ParticleSystemRenderMode.Billboard;
            if (material != null)
                renderer.sharedMaterial = material;
            renderer.sortingLayerID = sortingLayerId;
            renderer.sortingOrder = sortingOrder;
            return ps;
        }

        private static void Burst(ParticleSystem ps, int count)
        {
            var emission = ps.emission;
            emission.SetBursts(new[] { new ParticleSystem.Burst(0f, (short)count) });
        }

        private static void CircleShape(ParticleSystem ps, float radius)
        {
            var shape = ps.shape;
            shape.enabled = true;
            shape.shapeType = ParticleSystemShapeType.Circle;
            shape.radius = radius;
            shape.arc = 360f;
        }

        private static void FadeAndShrink(ParticleSystem ps, float drag, bool shrink = true)
        {
            var color = ps.colorOverLifetime;
            color.enabled = true;
            var gradient = new Gradient();
            gradient.SetKeys(new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(Color.white, 1f) },
                             new[] { new GradientAlphaKey(1f, 0f), new GradientAlphaKey(0.8f, 0.4f), new GradientAlphaKey(0f, 1f) });
            color.color = new ParticleSystem.MinMaxGradient(gradient);

            if (shrink)
            {
                var size = ps.sizeOverLifetime;
                size.enabled = true;
                size.size = new ParticleSystem.MinMaxCurve(1f, AnimationCurve.Linear(0f, 1f, 1f, 0.2f));
            }

            if (drag > 0f)
            {
                var limit = ps.limitVelocityOverLifetime;
                limit.enabled = true;
                limit.drag = drag;
                limit.multiplyDragByParticleSize = false;
                limit.multiplyDragByParticleVelocity = false;
            }
        }
    }
}
