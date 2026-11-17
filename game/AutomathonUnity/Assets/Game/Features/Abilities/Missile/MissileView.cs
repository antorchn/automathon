using UnityEngine;
using UnityEngine.VFX;

namespace Automathon.Game
{
    public class MissileView : EntityView<Missile>
    {
        /// <summary>Colour of a missile that is not armed yet (GameRules.MissileArmingDistance): a dull, see-through
        /// grey-blue instead of the armed missile's yellow, so that a missile that cannot explode yet reads as a dud.</summary>
        private static readonly Color UnarmedColor = new Color(0.62f, 0.68f, 0.78f, 0.55f);
        /// <summary>Size of a missile that is not armed yet, relative to an armed one.</summary>
        private const float UnarmedScale = 0.7f;
        /// <summary>When the missile arms, it pops to this size and shrinks back to its normal size in ArmPopDuration
        /// seconds, so that the moment it becomes dangerous is visible.</summary>
        private const float ArmPopScale = 1.8f;
        private const float ArmPopDuration = 0.2f;
        /// <summary>Radius of the small puff of a missile that fizzles out (collision before being armed).</summary>
        private const float FizzleRadius = 0.15f;

        [SerializeField] private float cameraShakeIntensity;
        [SerializeField] private float cameraShakeDuration;
        [SerializeField] private VisualEffect BigExplosion;

        private SpriteRenderer[] sprites;
        private Color[] baseColors;
        private Vector3 baseScale;
        private bool shownArmed = true;
        private float popTimeLeft;

        public override void Initialize(Missile entity)
        {
            base.Initialize(entity);
            // Missile colour is authored on the prefab's SpriteRenderer; its smoke-trail VFX is
            // disabled on the prefab (no trail behind the missile).
            sprites = GetComponentsInChildren<SpriteRenderer>();
            baseColors = new Color[sprites.Length];
            for (int i = 0; i < sprites.Length; i++)
                baseColors[i] = sprites[i].color;
            baseScale = transform.localScale;
            ShowArmed(entity.Armed, pop: false);
            SoundManager.instance.PlaySound("FireMissile");
        }

        private void Update()
        {
            if (Entity == null)
                return;
            if (Entity.Armed != shownArmed)
                ShowArmed(Entity.Armed, pop: Entity.Armed);
            if (popTimeLeft > 0f)
            {
                popTimeLeft = Mathf.Max(0f, popTimeLeft - Time.deltaTime);
                float t = popTimeLeft / ArmPopDuration;  // 1 when the missile arms, 0 at the end of the pop
                transform.localScale = baseScale * Mathf.Lerp(1f, ArmPopScale, t * t);
            }
        }

        /// <summary>A missile that cannot explode yet is drawn small, grey-blue and see-through; it takes its colour
        /// back with a pop when it arms.</summary>
        private void ShowArmed(bool armed, bool pop)
        {
            shownArmed = armed;
            for (int i = 0; i < sprites.Length; i++)
            {
                Color color = baseColors[i];
                if (!armed)
                    color = new Color(UnarmedColor.r, UnarmedColor.g, UnarmedColor.b, color.a * UnarmedColor.a);
                sprites[i].color = color;
            }
            popTimeLeft = pop ? ArmPopDuration : 0f;
            transform.localScale = baseScale * (pop ? ArmPopScale : armed ? 1f : UnarmedScale);
        }

        protected override void OnControllerDestroyed()
        {
            if (Entity.Fizzled)
            {
                // Collision before being armed: no damage, just a small puff
                if (ParticleFallback.Active) // browser: no VFX Graph
                    ParticleFallback.BigExplosion(transform.position, FizzleRadius);
                else
                {
                    VisualEffect puff = Instantiate(BigExplosion, transform.position, Quaternion.identity);
                    puff.SetFloat("Radius", FizzleRadius);
                }
                base.OnControllerDestroyed();
                return;
            }

            SoundManager.instance.PlaySound("ExplosionMissile");
            ScreenGlitch.TriggerHeavy();

            if (ParticleFallback.Active) // browser: no VFX Graph
                ParticleFallback.BigExplosion(transform.position, Missile.AOE_RADIUS.ToWorldLength());
            else
            {
                VisualEffect bigExplosion = Instantiate(BigExplosion, transform.position, Quaternion.identity);
                bigExplosion.SetFloat("Radius", Missile.AOE_RADIUS.ToWorldLength());
            }

            if (Camera.main != null)
                Camera.main.GetComponent<CameraShaker>().CameraShake(cameraShakeDuration, cameraShakeIntensity);

            // Leave a black scorch mark in the mud at the blast.
            Automathon.Game.View.MudLayerController.AddExplosionMark(transform.position, Missile.AOE_RADIUS.ToWorldLength());

            base.OnControllerDestroyed();
        }
    }
}
