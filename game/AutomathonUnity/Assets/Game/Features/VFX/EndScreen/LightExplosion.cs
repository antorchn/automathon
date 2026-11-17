using System;
using System.Collections.Generic;
using UnityEngine;
using Random = UnityEngine.Random;

public class LightExplosion : MonoBehaviour
{
    //Unscaled time everywhere: the end-of-game slow motion (Time.timeScale) must not slow the explosion down
    private float explosionStartTime;
    private float explosionEndTime;
    private bool isLightExplosionActive = false;

    [SerializeField] private Material lightExplosionMaterial;

    [Tooltip("Seconds, real time (not slowed down by Time.timeScale)")]
    [SerializeField] private float duration = 5f;
    [Tooltip("Moment (fraction of the explosion) when the screen is fully covered and nothing is visible underneath: " +
        "the arena is swapped for the end menu then. A Flash must be at 1 there, and every other effect over")]
    [SerializeField, Range(0f, 1f)] private float screenCoveredTime = 0.83f;

    private Action onScreenCovered, onFinished;

    //Launched from a world position: the epicenter is projected on screen again every frame, so the light stays on the
    //wreck while the camera shakes
    private Vector2 worldEpicenter;
    private bool followWorldEpicenter;

    [Header("Camera shake")]
    [Tooltip("Continuous tremor at full rumble - world units. Follows the rumble's volume (so it holds its breath with it " +
        "before the core blast) and stops when the screen is covered. The blasts' jolts are in their settings")]
    [SerializeField] private float tremorIntensity = 0.12f;
    [Tooltip("Speed of the tremor - shakes per second, roughly")]
    [SerializeField] private float tremorFrequency = 14f;

    private Transform shakenCamera;
    private CameraShaker cameraShaker;
    private Vector3 tremorOffset;

    [Header("Sound")]
    [Tooltip("Optional looping clip. Empty = a deep rumble generated in code")]
    [SerializeField] private AudioClip rumbleClip;
    [Tooltip("Explosion progress -> rumble volume: grows with the charging rays, cuts out just before the core blast " +
        "(the calm before the storm), peaks while the light covers the screen, dies with it")]
    [SerializeField] private AnimationCurve rumbleVolume = RecommendedRumbleVolume();
    [Tooltip("Explosion progress -> rumble pitch: rising with the tension")]
    [SerializeField] private AnimationCurve rumblePitch = RecommendedRumblePitch();
    [Tooltip("Explosion progress -> low-pass cutoff (Hz) of the rumble and the blasts: muffled since it's in space, " +
        "and even more right after the core blast, like deafened ears")]
    [SerializeField] private AnimationCurve muffleCutoff = RecommendedMuffleCutoff();
    [Tooltip("Faint high-pitched whine of the charging rays, the only high note of the explosion. 0 = none")]
    [SerializeField, Range(0f, 0.2f)] private float whineVolume = 0.03f;
    [SerializeField] private float whineFrequency = 2800f;
    [Tooltip("Explosion progress -> whine volume, as a fraction of whineVolume: rises while the charging rays grow out of " +
        "the wreck, alone in the silence before the core blast, swallowed by its boom")]
    [SerializeField] private AnimationCurve whineEnvelope = RecommendedWhineEnvelope();
    [Tooltip("Explosion progress -> whine pitch: rising while the rays charge")]
    [SerializeField] private AnimationCurve whinePitch = RecommendedWhinePitch();

    //the SoundManager sources playing the two loops, -1 when they don't play
    private int rumbleSoundId = -1, whineSoundId = -1;
    private AudioClip generatedRumble, generatedWhine;
    private float generatedWhineFrequency;

    public static LightExplosion instance;


    //General
    public static readonly int _epicenterXId = Shader.PropertyToID("_ExplosionEpicenterX");
    public static readonly int _epicenterYId = Shader.PropertyToID("_ExplosionEpicenterY");

    public static readonly int _ActivateBlastID = Shader.PropertyToID("_ActivateBlast");
    public static readonly int _ActivateRaysID = Shader.PropertyToID("_ActivateRays");
    public static readonly int _ActivateLargeRaysID = Shader.PropertyToID("_ActivateLargeRays");
    public static readonly int _ActivateFlashID = Shader.PropertyToID("_ActivateFlash");

    public static readonly int _BlastAdvancementID = Shader.PropertyToID("_BlastAdvancement");
    public static readonly int _RaysAdvancementID = Shader.PropertyToID("_RaysAdvancement");
    public static readonly int _LargeRaysAdvancementID = Shader.PropertyToID("_LargeRaysAdvancement");
    public static readonly int _FlashAdvancementID = Shader.PropertyToID("_FlashAdvancement");

    //Blast
    public static readonly int _ExplosionRangeID = Shader.PropertyToID("_ExplosionRange");
    public static readonly int _BlastRangeRelativeToCornerID = Shader.PropertyToID("_BlastRangeRelativeToCorner");
    public static readonly int _RingWidthID = Shader.PropertyToID("_RingWidth");
    public static readonly int _FadingSpeedID = Shader.PropertyToID("_FadingSpeed");
    public static readonly int _IntensityCenterRingID = Shader.PropertyToID("_IntensityCenterRing");
    public static readonly int _IntensityBorderRingID = Shader.PropertyToID("_IntensityBorderRing");
    public static readonly int _BlastProgressID = Shader.PropertyToID("_BlastProgress");
    public static readonly int _BlastIntensityID = Shader.PropertyToID("_BlastIntensity");
    public static readonly int _BlastInverseCurveID = Shader.PropertyToID("_BlastInverseCurve");
    public static readonly int _BlastHotColorID = Shader.PropertyToID("_BlastHotColor");
    public static readonly int _BlastCoolColorID = Shader.PropertyToID("_BlastCoolColor");
    public static readonly int _BlastShapeID = Shader.PropertyToID("_BlastShape");
    public static readonly int _BlastDistortionStrengthID = Shader.PropertyToID("_BlastDistortionStrength");
    public static readonly int _BlastDistortionWidthID = Shader.PropertyToID("_BlastDistortionWidth");
    public static readonly int _BlastDistortionChromaticID = Shader.PropertyToID("_BlastDistortionChromatic");

    private const int BlastShapeResolution = 256;
    private Texture2D blastShapeTexture;//one is enough: two blasts never run at the same time, it is re-baked at each start
    private readonly float[] blastShapeSamples = new float[BlastShapeResolution];
    private readonly List<Vector2> blastLobes = new List<Vector2>();//(angle, strength)

    //Flash
    public static readonly int _FlashColorID = Shader.PropertyToID("_FlashColor");

    //Rays and LargeRays
    public const int MaxRays = 12; //must match MAX_RAYS in the shader

    private RayBuffer raysBuffer, largeRaysBuffer;//created in Awake: Unity's API can't be used from field initializers


    [SerializeField] private List<LightExplosionEffectData> lightExplosionEffects;

    //baked at each launch, indexed like lightExplosionEffects (null for entries that aren't blasts)
    private Texture2D[] blastInverseCurves;

    public enum EffectType { Blast, Rays, LargeRays, Flash } //new types go at the end: the enum is serialized as an int

    [System.Serializable]
    public struct BlastShapeSettings
    {
        [Tooltip("Directions where the blast goes further and brighter. 0 (and not following the rays) = circular blast")]
        [Range(0, MaxRays)] public int lobeCount;
        [Tooltip("Put the lobes in the directions of the rays on screen (longer ray = bigger lobe) instead of new random ones")]
        public bool followRays;
        [Tooltip("Same as for the rays: max random deviation from an even angular spread - radians")]
        [Range(0f, Mathf.PI)] public float maxAngleDeviation;
        [Tooltip("Angular size of a lobe - radians")]
        public float lobeWidth;
        [Tooltip("0.2 = depending on the direction, the ring goes from 80% to 120% of the range")]
        [Range(0f, 0.9f)] public float rangeVariation;
        [Tooltip("0.5 = depending on the direction, the blast's energy goes from 50% to 150%")]
        [Range(0f, 1f)] public float brightnessVariation;
    }

    [System.Serializable]
    public struct BlastSettings
    {
        [Tooltip("Ring radius reached at local advancement 1 - 1 = from the center of the screen to a corner " +
            "(or the farthest corner from the epicenter, see below)")]
        public float range;
        [Tooltip("range is then a multiple of the distance from the epicenter to the farthest screen corner: " +
            "above 1 / (1 - rangeVariation), the ring leaves the screen in every direction, wherever the ship died")]
        public bool rangeRelativeToFarthestCorner;
        public BlastShapeSettings shape;
        [Tooltip("1 = from the center of the screen to a corner")]
        public float ringWidth;
        [Tooltip("Energy lost by the halo per unit of blast progress")]
        public float fadingSpeed;
        [Tooltip("Energies: 1 brings a black pixel to 63% of the color, 3 to 95%")]
        public float intensityCenterRing;
        public float intensityBorderRing;
        [Tooltip("Color of the ring and of the halo right behind it")]
        public Color hotColor;
        [Tooltip("Color the halo cools down to while it fades out")]
        public Color coolColor;
        [Tooltip("Blast progress -> multiplier of the whole blast (ring + halo). The radius curve has to stay increasing " +
            "(the halo uses its inverse), so fading out the blast is done here. Empty curve = always 1")]
        public AnimationCurve intensityOverProgress;
        [Tooltip("Played when the blast starts. Empty = silent")]
        public AudioClip sound;
        [Range(0f, 1f)] public float soundVolume;
        [Tooltip("Below 1: deeper and longer, for big blasts. 0 = 1")]
        public float soundPitch;
        [Tooltip("Camera jolt when the blast starts, in real seconds and world units (like CameraShaker). 0 = none")]
        public float shakeDuration, shakeIntensity;
        [Tooltip("Shockwave refraction around the ring: how far the image is pushed - 1 = screen center to corner. 0 = none")]
        public float distortionStrength;
        [Tooltip("Thickness of the distorted band around the ring - 1 = screen center to corner")]
        public float distortionWidth;
        [Tooltip("Colors split by the distortion, like a prism. 0 = none")]
        [Range(0f, 1f)] public float distortionChromaticSplit;
    }

    [System.Serializable]
    public struct RaysSettings
    {
        [Range(0, MaxRays)] public int count;
        [Tooltip("Max random deviation of each ray from an even angular spread - radians")]
        [Range(0f, Mathf.PI)] public float maxAngleDeviation;
        [Tooltip("1 = from the center of the screen to a corner, whatever the resolution or aspect ratio")]
        public float minLength, maxLength;
        [Tooltip("1 = from the center of the screen to a corner")]
        public float minWidth, maxWidth;
        public float lengthOverWidthRatio;
        public float widthDeviationMaxPercentage;
        [Tooltip("Energies: 1 brings a black pixel to 63% of the ray's color, 3 to 95%")]
        public float minIntensity, maxIntensity;
        [Tooltip("Each ray gets a random color between these two")]
        public Color colorA, colorB;
        [Tooltip("Brightness inside a ray: 1 = only fades along the ray, 0 = only fades across it")]
        [Range(0f, 1f)] public float lengthOverWidthIntensityWeight;
        public float intensityMaxVariationThroughTime;
        public float intensityVariationSpeed;
        [Tooltip("Max delay before a ray starts growing - 0.5 means it can start at the middle of the effect")]
        [Range(0f, 1f)] public float maxStartingAdvancementDelay;
        [Tooltip("Slow spin of the rays around the epicenter - radians per second, 0 = still")]
        public float rotationSpeed;
        [Tooltip("0.4 = each ray spins between 60% and 140% of rotationSpeed")]
        [Range(0f, 1f)] public float rotationSpeedVariation;
        [Tooltip("Clockwise or counter-clockwise at random, for each explosion")]
        public bool randomRotationDirection;
    }

    [System.Serializable]
    public struct FlashSettings
    {
        [Tooltip("The curve gives the brightness the flash brings a black pixel to, toward this color. " +
            "At 1, the whole screen is exactly this color")]
        public Color color;
    }

    [System.Serializable]
    public struct LightExplosionEffectData
    {
        public string name;//only a label: Unity shows the first string field as the element's title in the list
        public EffectType effectType;
        [Range(0f, 1f)] public float start;//global light explosion duration percentage
        [Range(0f, 1f)] public float end;
        public bool isActive;
        public AnimationCurve effectProgressToLocalAdvancement;
        [Tooltip("Only read by Blast effects, so that several blasts of different sizes can follow each other")]
        public BlastSettings blastSettings;
        [Tooltip("Only read by Rays and LargeRays effects")]
        public RaysSettings raysSettings;
        [Tooltip("Only read by Flash effects")]
        public FlashSettings flashSettings;
    };

    //Entries saved before a color field existed get (0,0,0,0), which would make their effect emit no light at all.
    //A color picked in the inspector has alpha 1, so alpha 0 is treated as "unset" -> white.
    private static Color OrWhite(Color color) => color.a == 0f ? Color.white : color;

    //Evenly spread around the circle from startingAngle, then randomly deviated: used for the rays and the blast lobes
    private static float SpreadAngle(float startingAngle, int i, int count, float maxDeviation)
        => startingAngle + 2f * Mathf.PI * i / count + (2f * Random.value - 1f) * maxDeviation;

    //Everything about a set of rays that is the same for all pixels. The random attributes are drawn once when the effect
    //starts, the advancement-dependent ones are recomputed once per frame, then both are sent to the shader as arrays.
    private class RayBuffer
    {
        private struct RayData
        {
            public float startAngle, angularSpeed;//radians, radians per second
            public float angle;//current one, updated by Upload
            public float length, width, intensity, delay, flickerPhase;
            public Color linearColor;
        }

        public int Count => count;
        public float Angle(int i) => rays[i].angle;//where the ray is on screen right now
        public float RelativeLength(int i) => rays[i].length / Mathf.Max(settings.maxLength, 1e-4f);

        private readonly int countId, lengthWeightId, geometryId, lightId;
        private readonly RayData[] rays = new RayData[MaxRays];
        //always sent whole: Unity fixes the size of a material array the first time it is set
        private readonly Vector4[] geometry = new Vector4[MaxRays];//(direction.x, direction.y, current length, current half width)
        private readonly Vector4[] lights = new Vector4[MaxRays];//rgb = linear color * current intensity
        private RaysSettings settings;
        private int count;
        private float startTime;

        public RayBuffer(string shaderPrefix)
        {
            countId = Shader.PropertyToID(shaderPrefix + "Count");
            lengthWeightId = Shader.PropertyToID(shaderPrefix + "LengthWeight");
            geometryId = Shader.PropertyToID(shaderPrefix + "Geometry");
            lightId = Shader.PropertyToID(shaderPrefix + "Light");
        }

        public void Generate(RaysSettings raysSettings, Material material, float time)
        {
            settings = raysSettings;
            count = Mathf.Clamp(settings.count, 0, MaxRays);
            startTime = time;

            float startingAngle = Random.value * 2f * Mathf.PI;
            //all the rays spin the same way, like a core turning on itself, each at its own speed
            float spinDirection = settings.randomRotationDirection && Random.value < 0.5f ? -1f : 1f;
            for (int i = 0; i < count; i++)
            {
                float angle = SpreadAngle(startingAngle, i, count, settings.maxAngleDeviation);
                float length = Mathf.Lerp(settings.minLength, settings.maxLength, Random.value);
                float widthRandomMultiplier = 1f + (2f * Random.value - 1f) * settings.widthDeviationMaxPercentage;

                rays[i] = new RayData
                {
                    startAngle = angle,
                    angle = angle,
                    angularSpeed = spinDirection * settings.rotationSpeed * (1f + (2f * Random.value - 1f) * settings.rotationSpeedVariation),
                    length = length,
                    width = Mathf.Clamp(widthRandomMultiplier * length / settings.lengthOverWidthRatio, settings.minWidth, settings.maxWidth),
                    intensity = Mathf.Lerp(settings.minIntensity, settings.maxIntensity, Random.value),
                    delay = Random.value * settings.maxStartingAdvancementDelay,
                    flickerPhase = Random.value * 2f * Mathf.PI,
                    //.linear by hand: unlike SetColor, SetVectorArray doesn't convert inspector (sRGB) colors to linear space
                    linearColor = Color.Lerp(OrWhite(settings.colorA), OrWhite(settings.colorB), Random.value).linear,
                };
            }

            material.SetInt(countId, count);
            material.SetFloat(lengthWeightId, settings.lengthOverWidthIntensityWeight);
            Upload(material, 0f, time);
        }

        public void Upload(Material material, float advancement, float time)
        {
            for (int i = 0; i < count; i++)
            {
                //a direction per ray and per frame (12 cos/sin for the whole screen): the rotation costs nothing per pixel
                rays[i].angle = rays[i].startAngle + rays[i].angularSpeed * (time - startTime);
                RayData ray = rays[i];
                //each ray waits for its own delay, then grows over the rest of the effect
                float localAdvancement = advancement > ray.delay ? (advancement - ray.delay) / Mathf.Max(1f - ray.delay, 1e-4f) : 0f;
                float flicker = Mathf.Sin(time * settings.intensityVariationSpeed + ray.flickerPhase) * settings.intensityMaxVariationThroughTime;

                geometry[i] = new Vector4(Mathf.Cos(ray.angle), Mathf.Sin(ray.angle), localAdvancement * ray.length, localAdvancement * ray.width / 2f);
                float intensity = localAdvancement > 0f ? Mathf.Max(0f, localAdvancement * ray.intensity + flicker) : 0f;
                lights[i] = (Vector4)(ray.linearColor * intensity);
            }

            material.SetVectorArray(geometryId, geometry);
            material.SetVectorArray(lightId, lights);
        }
    }

    private RayBuffer RayBufferFor(EffectType effectType) => effectType switch
    {
        EffectType.Rays => raysBuffer,
        EffectType.LargeRays => largeRaysBuffer,
        _ => null,
    };

    private void Awake()
    {
        instance = this;
        raysBuffer = new RayBuffer("_Rays");
        largeRaysBuffer = new RayBuffer("_LargeRays");
    }

    private void OnEnable()
    {
        StopAllEffects();
    }

    private void OnDisable()
    {
        lightExplosionMaterial.SetFloat(_epicenterXId, 0f);
        lightExplosionMaterial.SetFloat(_epicenterYId, 0f);
        StopAllEffects();
    }

    private void OnDestroy()
    {
        ReleaseBlastInverseCurves();
        if (blastShapeTexture != null)
            Destroy(blastShapeTexture);
        if (generatedRumble != null)
            Destroy(generatedRumble);
        if (generatedWhine != null)
            Destroy(generatedWhine);
    }

    private void StopAllEffects()
    {
        isLightExplosionActive = false;
        followWorldEpicenter = false;
        ApplyTremor(0f);
        //the blasts' booms are left to ring out
        StopLoop(ref rumbleSoundId);
        StopLoop(ref whineSoundId);

        foreach (EffectType effectType in System.Enum.GetValues(typeof(EffectType)))
            StopEffect(effectType);

        for (int i = 0; i < lightExplosionEffects.Count; i++)
        {
            LightExplosionEffectData effect = lightExplosionEffects[i];
            effect.isActive = false; //struct copy: has to be written back, otherwise the next explosion never calls StartEffect
            lightExplosionEffects[i] = effect;
        }
    }

    /// <summary>Stops the explosion at once, without calling its callbacks (a replay started during the end of a match).</summary>
    public void Cancel()
    {
        onScreenCovered = null;
        onFinished = null;
        StopAllEffects();
    }

    /// <summary>Explosion at a fixed point of the screen (uv, 0 to 1 on both axes).</summary>
    /// <param name="onScreenCovered">Called once at screenCoveredTime, when nothing behind the light is visible: the moment to change the scene.</param>
    /// <param name="onFinished">Called once when the explosion is over and the screen is back to normal.</param>
    public void LaunchLightExplosion(float epicenterX, float epicenterY, Action onScreenCovered = null, Action onFinished = null)
        => Launch(new Vector2(epicenterX, epicenterY), onScreenCovered, onFinished);

    /// <summary>Explosion at a point of the world: it stays on that point on screen, even while the camera shakes.</summary>
    public void LaunchLightExplosionAt(Vector2 worldPosition, Action onScreenCovered = null, Action onFinished = null)
    {
        Launch(WorldToEpicenterUV(worldPosition), onScreenCovered, onFinished);
        worldEpicenter = worldPosition;
        followWorldEpicenter = true;
    }

    private void Launch(Vector2 epicenterUV, Action onScreenCovered, Action onFinished)
    {
        StopAllEffects(); //a relaunch restarts the timeline from scratch, even if the previous explosion wasn't over

        SetEpicenter(epicenterUV);
        explosionStartTime = Time.unscaledTime;
        explosionEndTime = explosionStartTime + duration;
        this.onScreenCovered = onScreenCovered;
        this.onFinished = onFinished;

        BakeBlastInverseCurves();

        Camera mainCamera = Camera.main;
        shakenCamera = mainCamera != null ? mainCamera.transform : null;
        cameraShaker = mainCamera != null ? mainCamera.GetComponent<CameraShaker>() : null;

        if (rumbleClip == null && generatedRumble == null)
            generatedRumble = CreateRumbleClip();
        float wantedWhineFrequency = Mathf.Max(1f, Mathf.Round(whineFrequency));
        if (generatedWhine == null || generatedWhineFrequency != wantedWhineFrequency) //regenerated if tuned meanwhile
        {
            if (generatedWhine != null)
                Destroy(generatedWhine);
            generatedWhineFrequency = wantedWhineFrequency;
            generatedWhine = CreateWhineClip(generatedWhineFrequency);
        }

        //both loops start silent: their volumes and pitches follow the curves in Update
        rumbleSoundId = SoundManager.instance.PlayClip(rumbleClip != null ? rumbleClip : generatedRumble, 0f, loop: true, muffled: true);
        whineSoundId = SoundManager.instance.PlayClip(generatedWhine, 0f, loop: true);

        isLightExplosionActive = true;
    }

    private static void StopLoop(ref int soundId)
    {
        if (soundId >= 0 && SoundManager.instance != null) //it can be destroyed first when the scene unloads
            SoundManager.instance.StopSound(soundId);
        soundId = -1;
    }

    private void SetEpicenter(Vector2 uv)
    {
        lightExplosionMaterial.SetFloat(_epicenterXId, uv.x);
        lightExplosionMaterial.SetFloat(_epicenterYId, uv.y);
    }

    //Same convention as the uv of the pass (texture coordinates of the camera image): origin at the bottom left, like
    //WorldToScreenPoint. Flipping y put the light on the mirror point of the wreck (only right for a ship dying at y = 0).
    private static Vector2 WorldToEpicenterUV(Vector2 worldPosition)
    {
        Camera mainCamera = Camera.main;
        if (mainCamera == null)
            return new Vector2(0.5f, 0.5f);

        Vector3 screenPosition = mainCamera.WorldToScreenPoint(worldPosition);
        return new Vector2(screenPosition.x / Screen.width, screenPosition.y / Screen.height);
    }

    private void PlayBlastSound(BlastSettings settings)
    {
        if (settings.sound == null)
            return;

        //on a source of its own (the next one), so its pitch doesn't change the first boom, still ringing
        SoundManager.instance.PlayClip(settings.sound, settings.soundVolume,
            pitch: settings.soundPitch > 0f ? settings.soundPitch : 1f, muffled: true);
    }

    private float RumbleLevel(float t) => Mathf.Clamp01(EvaluateOrOne(rumbleVolume, t));

    private void UpdateSound(float t)
    {
        SoundManager soundManager = SoundManager.instance;
        //the rumble and the booms still ringing
        soundManager.MuffleCutoff = muffleCutoff != null && muffleCutoff.length > 0 ? muffleCutoff.Evaluate(t) : 22000f;

        soundManager.SetVolume(rumbleSoundId, RumbleLevel(t));
        soundManager.SetPitch(rumbleSoundId, Mathf.Max(0.01f, EvaluateOrOne(rumblePitch, t)));

        float whine = whineEnvelope != null && whineEnvelope.length > 0 ? Mathf.Clamp01(whineEnvelope.Evaluate(t)) : 0f;
        soundManager.SetVolume(whineSoundId, whineVolume * whine);
        soundManager.SetPitch(whineSoundId, Mathf.Max(0.01f, EvaluateOrOne(whinePitch, t)));
    }

    private void ApplyTremor(float amplitude)
    {
        if (shakenCamera == null)
        {
            tremorOffset = Vector3.zero;
            return;
        }

        Vector3 next = Vector3.zero;
        if (amplitude > 0f)
        {
            //Perlin noise: a rolling tremor rather than a random jump every frame
            float time = Time.unscaledTime * tremorFrequency;
            next = new Vector3(Mathf.PerlinNoise(time, 0.37f) - 0.5f, Mathf.PerlinNoise(0.71f, time) - 0.5f, 0f) * (2f * amplitude);
        }

        //relative, like CameraShaker's shakes, so they add up instead of overwriting each other
        shakenCamera.localPosition += next - tremorOffset;
        tremorOffset = next;
    }

    //A pure high note: a sine with a whole number of periods in one second, so the 1 s clip loops seamlessly (at any
    //pitch: the pitch only changes the playback speed). The phase is computed in integers, exactly, instead of sin of an
    //ever growing float.
    private static AudioClip CreateWhineClip(float frequency, int sampleRate = 44100)
    {
        long wholeFrequency = (long)frequency;
        float[] samples = new float[sampleRate];
        for (int i = 0; i < sampleRate; i++)
            samples[i] = 0.5f * Mathf.Sin(2f * Mathf.PI * (wholeFrequency * i % sampleRate) / sampleRate);

        AudioClip clip = AudioClip.Create("Generated light explosion whine", sampleRate, 1, sampleRate, false);
        clip.SetData(samples, 0);
        return clip;
    }

    //A deep rumble generated in code, used when no rumbleClip is assigned: brown noise (a random walk, so mostly low
    //frequencies), low-passed again, with a slow swell so it rolls instead of hissing. Loops seamlessly: its end is
    //cross-faded into its start.
    private static AudioClip CreateRumbleClip(float loopSeconds = 4f, int sampleRate = 44100)
    {
        int loop = (int)(loopSeconds * sampleRate);
        int fade = sampleRate / 2;
        float[] generated = new float[loop + fade];

        System.Random random = new System.Random(20261116);
        float brown = 0f, lowPassed = 0f;
        float lowPassK = 1f - Mathf.Exp(-2f * Mathf.PI * 250f / sampleRate); //one-pole low-pass at 250 Hz
        for (int i = 0; i < generated.Length; i++)
        {
            float white = (float)(random.NextDouble() * 2.0 - 1.0);
            brown = (brown + 0.02f * white) * 0.998f; //leaky, so it stays around 0
            lowPassed += lowPassK * (brown - lowPassed);
            float t = i / (float)sampleRate;
            //swell frequencies are whole numbers of cycles per loop, so they join at the loop point too
            float swell = 0.75f + 0.25f * Mathf.Sin(2f * Mathf.PI * 2f * t / loopSeconds) * Mathf.Sin(2f * Mathf.PI * 5f * t / loopSeconds);
            generated[i] = lowPassed * swell;
        }

        float[] samples = new float[loop];
        float peak = 1e-6f;
        for (int i = 0; i < loop; i++)
        {
            float k = i < fade ? i / (float)fade : 1f;
            samples[i] = i < fade ? generated[i] * k + generated[loop + i] * (1f - k) : generated[i];
            peak = Mathf.Max(peak, Mathf.Abs(samples[i]));
        }
        for (int i = 0; i < loop; i++)
            samples[i] *= 0.9f / peak;

        AudioClip clip = AudioClip.Create("Generated light explosion rumble", loop, 1, sampleRate, false);
        clip.SetData(samples, 0);
        return clip;
    }

    private static int ActivateId(EffectType effectType) => effectType switch
    {
        EffectType.Blast => _ActivateBlastID,
        EffectType.Rays => _ActivateRaysID,
        EffectType.LargeRays => _ActivateLargeRaysID,
        _ => _ActivateFlashID,
    };

    private static int AdvancementId(EffectType effectType) => effectType switch
    {
        EffectType.Blast => _BlastAdvancementID,
        EffectType.Rays => _RaysAdvancementID,
        EffectType.LargeRays => _LargeRaysAdvancementID,
        _ => _FlashAdvancementID,
    };

    private void StopEffect(EffectType effectType)
    {
        lightExplosionMaterial.SetInt(ActivateId(effectType), 0);
        lightExplosionMaterial.SetFloat(AdvancementId(effectType), 0f);
    }

    private void StartEffect(int effectIndex)
    {
        LightExplosionEffectData effect = lightExplosionEffects[effectIndex];

        //effects of the same type share the shader's properties: each one pushes its own parameters when it starts
        if (effect.effectType == EffectType.Blast)
        {
            SetBlastParams(effect.blastSettings, blastInverseCurves[effectIndex]);
            PlayBlastSound(effect.blastSettings);
            if (cameraShaker != null && effect.blastSettings.shakeDuration > 0f && effect.blastSettings.shakeIntensity > 0f)
                cameraShaker.CameraShake(effect.blastSettings.shakeDuration, effect.blastSettings.shakeIntensity, unscaledTime: true);
        }
        else if (effect.effectType == EffectType.Flash)
            lightExplosionMaterial.SetColor(_FlashColorID, OrWhite(effect.flashSettings.color)); //SetColor converts to linear itself
        else //new random rays at each start
            RayBufferFor(effect.effectType)?.Generate(effect.raysSettings, lightExplosionMaterial, Time.unscaledTime);

        lightExplosionMaterial.SetInt(ActivateId(effect.effectType), 1);
        lightExplosionMaterial.SetFloat(AdvancementId(effect.effectType), 0f);
    }

    private void SetBlastParams(BlastSettings settings, Texture2D inverseCurve)
    {
        lightExplosionMaterial.SetFloat(_ExplosionRangeID, settings.range);
        //the farthest corner is measured by the shader each frame, so it stays right if the window is resized meanwhile
        lightExplosionMaterial.SetInt(_BlastRangeRelativeToCornerID, settings.rangeRelativeToFarthestCorner ? 1 : 0);
        lightExplosionMaterial.SetTexture(_BlastShapeID, BakeBlastShape(settings.shape));
        lightExplosionMaterial.SetFloat(_RingWidthID, settings.ringWidth);
        lightExplosionMaterial.SetFloat(_FadingSpeedID, settings.fadingSpeed);
        lightExplosionMaterial.SetFloat(_IntensityCenterRingID, settings.intensityCenterRing);
        lightExplosionMaterial.SetFloat(_IntensityBorderRingID, settings.intensityBorderRing);
        lightExplosionMaterial.SetTexture(_BlastInverseCurveID, inverseCurve);
        lightExplosionMaterial.SetColor(_BlastHotColorID, OrWhite(settings.hotColor));
        lightExplosionMaterial.SetColor(_BlastCoolColorID, OrWhite(settings.coolColor));
        lightExplosionMaterial.SetFloat(_BlastDistortionStrengthID, settings.distortionStrength);
        lightExplosionMaterial.SetFloat(_BlastDistortionWidthID, settings.distortionWidth);
        lightExplosionMaterial.SetFloat(_BlastDistortionChromaticID, settings.distortionChromaticSplit);
    }

    //Bakes the blast's shape over the angle: r = range multiplier, g = brightness multiplier
    private Texture2D BakeBlastShape(BlastShapeSettings shape)
    {
        if (blastShapeTexture == null)
        {
            blastShapeTexture = new Texture2D(BlastShapeResolution, 1, TextureFormat.RGHalf, false, true)
            {
                wrapMode = TextureWrapMode.Repeat, //the angle loops: -PI and PI are the same direction
                filterMode = FilterMode.Bilinear
            };
        }

        //lobes in the directions of the rays on screen, or new ones generated the same way as the rays
        blastLobes.Clear();
        if (shape.followRays && raysBuffer.Count > 0)
        {
            for (int i = 0; i < raysBuffer.Count; i++)
                blastLobes.Add(new Vector2(raysBuffer.Angle(i), raysBuffer.RelativeLength(i)));
        }
        else
        {
            float startingAngle = Random.value * 2f * Mathf.PI;
            for (int i = 0; i < shape.lobeCount; i++)
                blastLobes.Add(new Vector2(SpreadAngle(startingAngle, i, shape.lobeCount, shape.maxAngleDeviation), Random.Range(0.5f, 1f)));
        }

        //m(angle) = sum of strength * exp((cos(angle - lobeAngle) - 1) / lobeWidth^2): a smooth bump around each lobe
        //(close to a gaussian of standard deviation lobeWidth), periodic so that -PI and PI join
        float sharpness = 1f / Mathf.Max(shape.lobeWidth * shape.lobeWidth, 1e-4f);
        float mean = 0f;
        for (int t = 0; t < BlastShapeResolution; t++)
        {
            float angle = (t + 0.5f) / BlastShapeResolution * 2f * Mathf.PI - Mathf.PI; //texel centers, matching atan2 in the shader
            float m = 0f;
            foreach (Vector2 lobe in blastLobes)
                m += lobe.y * Mathf.Exp((Mathf.Cos(angle - lobe.x) - 1f) * sharpness);
            blastShapeSamples[t] = m;
            mean += m / BlastShapeResolution;
        }

        //centered and scaled to [-1, 1], so that rangeVariation and brightnessVariation are exact bounds
        float maxDeviation = 0f;
        for (int t = 0; t < BlastShapeResolution; t++)
            maxDeviation = Mathf.Max(maxDeviation, Mathf.Abs(blastShapeSamples[t] - mean));

        for (int t = 0; t < BlastShapeResolution; t++)
        {
            float n = maxDeviation > 1e-6f ? (blastShapeSamples[t] - mean) / maxDeviation : 0f; //no lobe -> 0 -> circle
            blastShapeTexture.SetPixel(t, 0, new Color(1f + shape.rangeVariation * n, 1f + shape.brightnessVariation * n, 0f));
        }
        blastShapeTexture.Apply(false, false); //stays readable: it is re-baked at each blast start
        return blastShapeTexture;
    }

    private static bool IsInWindow(LightExplosionEffectData effect, float t) => t >= effect.start && t <= effect.end;

    private void Update()
    {
        if (!isLightExplosionActive)
            return;

        float t = (Time.unscaledTime - explosionStartTime) / (explosionEndTime - explosionStartTime);

        if (followWorldEpicenter)
            SetEpicenter(WorldToEpicenterUV(worldEpicenter));

        UpdateSound(Mathf.Min(t, 1f));
        //the tremor follows the rumble, and stops once the screen is covered: the end menu appears under the light
        ApplyTremor(t < screenCoveredTime ? tremorIntensity * RumbleLevel(t) : 0f);

        //each callback is cleared before being called, so it runs once even if it relaunches an explosion
        if (t >= screenCoveredTime && onScreenCovered != null)
        {
            Action callback = onScreenCovered;
            onScreenCovered = null;
            callback();
        }

        if (t > 1f)
        {
            StopAllEffects();
            Action callback = onFinished;
            onFinished = null;
            callback?.Invoke();
            return;
        }

        //Two passes: every effect leaving its window is stopped before any effect entering its window is started.
        //With a single pass, a blast ending this frame could switch off the shader slot that the following blast
        //has just switched on, depending on their order in the list.
        for (int i = 0; i < lightExplosionEffects.Count; i++)
        {
            LightExplosionEffectData effect = lightExplosionEffects[i];
            if (effect.isActive && !IsInWindow(effect, t))
            {
                StopEffect(effect.effectType);
                effect.isActive = false;
                lightExplosionEffects[i] = effect;
            }
        }

        for (int i = 0; i < lightExplosionEffects.Count; i++)
        {
            LightExplosionEffectData effect = lightExplosionEffects[i];
            if (!IsInWindow(effect, t))
                continue;

            if (!effect.isActive)
            {
                StartEffect(i);
                effect.isActive = true;
                lightExplosionEffects[i] = effect;
            }

            //update l'avancement local de l'effet actif
            float progress = (t - effect.start) / (effect.end - effect.start);
            float localAdvancement = effect.effectProgressToLocalAdvancement.Evaluate(progress);
            lightExplosionMaterial.SetFloat(AdvancementId(effect.effectType), localAdvancement);

            if (effect.effectType == EffectType.Blast)
            {
                //the halo needs the time itself (not only the radius) to know how long ago the ring left each pixel
                lightExplosionMaterial.SetFloat(_BlastProgressID, progress);
                lightExplosionMaterial.SetFloat(_BlastIntensityID, EvaluateOrOne(effect.blastSettings.intensityOverProgress, progress));
            }
            else
            {
                //once per frame for the whole screen, instead of once per pixel
                RayBufferFor(effect.effectType)?.Upload(lightExplosionMaterial, localAdvancement, Time.unscaledTime);
            }
        }
    }

    //entries saved before a curve field existed get an empty curve, which would evaluate to 0 and hide the effect
    private static float EvaluateOrOne(AnimationCurve curve, float x) => curve == null || curve.length == 0 ? 1f : curve.Evaluate(x);

    private void BakeBlastInverseCurves()
    {
        ReleaseBlastInverseCurves();

        blastInverseCurves = new Texture2D[lightExplosionEffects.Count];
        for (int i = 0; i < lightExplosionEffects.Count; i++)
        {
            if (lightExplosionEffects[i].effectType == EffectType.Blast)
                blastInverseCurves[i] = BakeInverseCurve(lightExplosionEffects[i].effectProgressToLocalAdvancement, 256);
        }
    }

    private void ReleaseBlastInverseCurves()
    {
        if (blastInverseCurves == null)
            return;

        foreach (Texture2D texture in blastInverseCurves)
        {
            if (texture != null)
                Destroy(texture);
        }
        blastInverseCurves = null;
    }

    private static Texture2D BakeInverseCurve(AnimationCurve f, int size)
    {
        Texture2D texture = new Texture2D(size, 1, TextureFormat.RHalf, false, true)
        {
            wrapMode = TextureWrapMode.Clamp,
            filterMode = FilterMode.Bilinear
        };

        for (int i = 0; i < size; i++)
        {
            float y = i / (size - 1f); // the local advancement stored in this texel
            float low = 0f, high = 1f;

            for (int iteration = 0; iteration < 20; iteration++)
            {
                float mid = (low + high) / 2f;
                if (f.Evaluate(mid) < y) low = mid;
                else high = mid;
            }

            texture.SetPixel(i, 0, new Color((low + high) / 2f, 0f, 0f));
        }

        texture.Apply(false, true);
        return texture;
    }

    private void OnValidate()
    {
        if (lightExplosionEffects == null)
            return;

        for (int i = 0; i < lightExplosionEffects.Count; i++)
        {
            LightExplosionEffectData effect = lightExplosionEffects[i];
            if (effect.effectType == EffectType.Blast && !IsStrictlyIncreasing(effect.effectProgressToLocalAdvancement))
                Debug.LogWarning($"[LightExplosion] Blast '{effect.name}': its radius curve must be strictly increasing, the halo is computed " +
                    "with its inverse. Fade the blast out with blastSettings.intensityOverProgress instead.", this);

            for (int j = i + 1; j < lightExplosionEffects.Count; j++)
            {
                LightExplosionEffectData a = lightExplosionEffects[i];
                LightExplosionEffectData b = lightExplosionEffects[j];
                if (a.effectType == b.effectType && a.start < b.end && b.start < a.end)
                    Debug.LogWarning($"[LightExplosion] '{a.name}' and '{b.name}' are both {a.effectType} effects with overlapping windows: " +
                        "they share the same shader properties, so they can't run at the same time.", this);
            }

            if ((effect.effectType == EffectType.Rays || effect.effectType == EffectType.LargeRays) && effect.raysSettings.count == 0)
                Debug.LogWarning($"[LightExplosion] '{effect.name}' has no rays (raysSettings.count = 0). The ray settings moved from " +
                    "the material to each effect: fill them in, or use \"Load recommended timeline\".", this);
        }

        if (lightExplosionEffects.Count > 0 && !IsScreenFullyCoveredAt(screenCoveredTime))
            Debug.LogWarning($"[LightExplosion] No Flash is at 1 at screenCoveredTime ({screenCoveredTime}): the arena would visibly " +
                "disappear when it is swapped for the end menu.", this);
    }

    private bool IsScreenFullyCoveredAt(float t)
    {
        foreach (LightExplosionEffectData effect in lightExplosionEffects)
        {
            if (effect.effectType == EffectType.Flash && IsInWindow(effect, t) && effect.end > effect.start
                && effect.effectProgressToLocalAdvancement.Evaluate((t - effect.start) / (effect.end - effect.start)) >= 0.999f)
                return true;
        }
        return false;
    }

    private static bool IsStrictlyIncreasing(AnimationCurve curve, int samples = 64)
    {
        if (curve == null || curve.length < 2)
            return false;

        float previous = curve.Evaluate(0f);
        for (int i = 1; i <= samples; i++)
        {
            float current = curve.Evaluate(i / (float)samples);
            if (current <= previous)
                return false;
            previous = current;
        }
        return true;
    }

    //Play mode only: replays the whole explosion at the center of the screen, to tune it without finishing a game
    [ContextMenu("Preview explosion at screen center")]
    private void PreviewExplosion() => LaunchLightExplosion(0.5f, 0.5f);

    //Inspector: right click on the component's header > "Load recommended timeline". Overwrites the effect list (undoable).
    [ContextMenu("Load recommended timeline")]
    private void LoadRecommendedTimeline()
    {
#if UNITY_EDITOR
        UnityEditor.Undo.RecordObject(this, "Load recommended light explosion timeline");
#endif
        duration = 5f;
        //the whiteout is at 1 from 0.74 to 0.84, and everything under it is over at 0.82
        screenCoveredTime = 0.83f;
        rumbleVolume = RecommendedRumbleVolume();
        rumblePitch = RecommendedRumblePitch();
        muffleCutoff = RecommendedMuffleCutoff();
        whineEnvelope = RecommendedWhineEnvelope();
        whinePitch = RecommendedWhinePitch();
        whineVolume = 0.03f;
        whineFrequency = 2800f;
        tremorIntensity = 0.12f; //a missile explosion jolts the camera by 0.5, a shot by 0.07
        tremorFrequency = 14f;

        lightExplosionEffects = new List<LightExplosionEffectData>
        {
            //Cold and saturated palette, matching the project's space / neon colors: cyan, electric blue, violet for the
            //halos about to go out. Only the most intense spots get close to white (energy), the rest keeps its color.

            //1) Destruction: the ship dies with a short flash and a medium, almost circular blast
            Flash("Impact flash", 0.00f, 0.06f,
                new AnimationCurve(new Keyframe(0f, 0.35f, 0f, -0.9f), new Keyframe(1f, 0f, 0f, 0f)),
                new Color(0.45f, 0.85f, 1f)),
            Blast("Destruction blast", 0.00f, 0.22f, EaseOut(2f), new BlastSettings
            {
                range = 0.38f, ringWidth = 0.012f, fadingSpeed = 2.1f, intensityCenterRing = 1.8f, intensityBorderRing = 0.7f,
                shape = new BlastShapeSettings
                {
                    lobeCount = 5, maxAngleDeviation = 0.4f, lobeWidth = 0.6f, rangeVariation = 0.07f, brightnessVariation = 0.25f
                },
                hotColor = new Color(0.4f, 0.9f, 1f), coolColor = new Color(0.35f, 0.3f, 1f),
                intensityOverProgress = HoldThenFadeOut(0.35f),
                sound = LoadClip("Assets/Game/Features/SFX/NormedSFX/Explosion_Normed_01.wav"), soundVolume = 0.8f, soundPitch = 1f,
                shakeDuration = 0.4f, shakeIntensity = 0.35f,
                distortionStrength = 0.012f, distortionWidth = 0.035f, distortionChromaticSplit = 0.35f,
            }),

            //2) Charge: thin rays leak out of the wreck, slowly at first then faster, reaching full length right when the
            //core blows (t = 0.55), while slowly spinning like an unstable core
            Rays("Charging rays", EffectType.Rays, 0.10f, 0.80f,
                new AnimationCurve(new Keyframe(0f, 0f, 0f, 0.4f), new Keyframe(0.45f, 0.3f, 1.5f, 1.5f),
                    new Keyframe(0.64f, 1f, 4f, 0f), new Keyframe(1f, 1f, 0f, 0f)),
                RaysSettingsFromMaterial(new Color(0.3f, 0.85f, 1f), new Color(0.5f, 0.6f, 1f), rotationSpeed: 0.12f)),

            //3) Core explosion: a blast leaving the screen in every direction, bulging slightly where the charging rays were,
            //together with the large rays. Its halo fades fast (only a glow trailing the ring), otherwise it whitens
            //the whole disc and hides the large rays.
            Blast("Core blast", 0.55f, 0.80f, EaseOut(3f), new BlastSettings
            {
                range = 1.3f, rangeRelativeToFarthestCorner = true, //1.3 * (1 - 0.06) > 1: out of the screen everywhere
                ringWidth = 0.08f, fadingSpeed = 12f, intensityCenterRing = 3.5f, intensityBorderRing = 0.9f,
                shape = new BlastShapeSettings
                {
                    followRays = true, lobeCount = 7, maxAngleDeviation = 0.5f, //lobeCount & deviation: only if there are no rays
                    lobeWidth = 0.45f, rangeVariation = 0.06f, brightnessVariation = 0.3f
                },
                hotColor = new Color(0.5f, 0.92f, 1f), coolColor = new Color(0.3f, 0.35f, 1f),
                intensityOverProgress = HoldThenFadeOut(0.7f), //the whiteout already covers it by then, this is only a safety
                //pitched down: deeper and stretched to ~2.7 s, a much bigger boom than the first one
                sound = LoadClip("Assets/Game/Features/SFX/NormedSFX/Explosion_Normed_02.wav"), soundVolume = 1f, soundPitch = 0.6f,
                shakeDuration = 1f, shakeIntensity = 0.8f,
                distortionStrength = 0.03f, distortionWidth = 0.1f, distortionChromaticSplit = 0.4f,
            }),
            Rays("Large rays", EffectType.LargeRays, 0.55f, 0.82f, EaseOut(),
                RaysSettingsFromMaterial(new Color(0.4f, 0.88f, 1f), new Color(0.3f, 0.55f, 1f))),

            //4) Whiteout: reaches 1 (the screen is then exactly its color), holds while every effect above stops
            //underneath it and the arena is swapped for the end menu, then fades away and reveals the menu
            Flash("Whiteout", 0.60f, 1.00f,
                new AnimationCurve(new Keyframe(0f, 0f, 0f, 0f), new Keyframe(0.35f, 1f, 0f, 0f),
                    new Keyframe(0.6f, 1f, 0f, 0f), new Keyframe(1f, 0f, 0f, 0f)),
                new Color(0.6f, 0.92f, 1f)),
        };
    }

    //Fast start, slow end, and strictly increasing so it can be inverted. Hermite from (0,0) with slope startSlope to (1,1)
    //with slope 0: 3 gives 1 - (1 - x)^3, 2 gives the gentler 1 - (1 - x)^2. Stays increasing for startSlope in ]0, 3].
    private static AnimationCurve EaseOut(float startSlope = 3f)
        => new AnimationCurve(new Keyframe(0f, 0f, 0f, startSlope), new Keyframe(1f, 1f, 0f, 0f));

    //1 until holdUntil, then smoothly down to 0 at 1
    private static AnimationCurve HoldThenFadeOut(float holdUntil)
        => new AnimationCurve(new Keyframe(0f, 1f, 0f, 0f), new Keyframe(holdUntil, 1f, 0f, 0f), new Keyframe(1f, 0f, 0f, 0f));

    private static LightExplosionEffectData Effect(string name, EffectType effectType, float start, float end, AnimationCurve curve)
        => new LightExplosionEffectData { name = name, effectType = effectType, start = start, end = end, effectProgressToLocalAdvancement = curve };

    private static LightExplosionEffectData Rays(string name, EffectType effectType, float start, float end, AnimationCurve curve, RaysSettings settings)
    {
        LightExplosionEffectData effect = Effect(name, effectType, start, end, curve);
        effect.raysSettings = settings;
        return effect;
    }

    private static LightExplosionEffectData Flash(string name, float start, float end, AnimationCurve curve, Color color)
    {
        LightExplosionEffectData effect = Effect(name, EffectType.Flash, start, end, curve);
        effect.flashSettings = new FlashSettings { color = color };
        return effect;
    }

    //Based on the values the material had when the ray settings moved out of it (shared by rays and large rays back then),
    //intensities as energies (a bit higher for cold colors, less luminous than warm ones), lengths tuned in the
    //"1 = screen center to corner" unit
    private static RaysSettings RaysSettingsFromMaterial(Color colorA, Color colorB, float rotationSpeed = 0f) => new RaysSettings
    {
        rotationSpeed = rotationSpeed,
        rotationSpeedVariation = 0.4f,
        randomRotationDirection = true,
        count = 12,
        maxAngleDeviation = 1f,
        minLength = 0.07f, maxLength = 1.05f,
        minWidth = 0.0001f, maxWidth = 0.01f,
        lengthOverWidthRatio = 20f,
        widthDeviationMaxPercentage = 0.2f,
        minIntensity = 0.1f, maxIntensity = 1.4f,
        colorA = colorA, colorB = colorB,
        lengthOverWidthIntensityWeight = 0.8f,
        intensityMaxVariationThroughTime = 0.15f,
        intensityVariationSpeed = 2f,
        maxStartingAdvancementDelay = 0.2f,
    };

    private static LightExplosionEffectData Blast(string name, float start, float end, AnimationCurve radiusCurve, BlastSettings settings)
    {
        LightExplosionEffectData effect = Effect(name, EffectType.Blast, start, end, radiusCurve);
        effect.blastSettings = settings;
        return effect;
    }

    //Explosion progress -> rumble volume: barely there when the charging rays appear (0.10), swelling faster and faster,
    //then cut short just before the core blast: about 0.2 s of silence (0.505 - 0.55, the calm before the storm) that
    //makes the boom hit harder. Back at once with it, full while the light covers the screen (0.74 - 0.84), dying with the light.
    private static AnimationCurve RecommendedRumbleVolume() => new AnimationCurve(
        new Keyframe(0f, 0f, 0f, 0f), new Keyframe(0.10f, 0.04f, 0.3f, 0.3f), new Keyframe(0.50f, 0.5f, 1.6f, 0f),
        new Keyframe(0.52f, 0f, 0f, 0f), new Keyframe(0.55f, 0f, 0f, 0f), new Keyframe(0.565f, 0.85f, 0f, 0f),
        new Keyframe(0.74f, 1f, 0f, 0f), new Keyframe(0.84f, 1f, 0f, 0f), new Keyframe(1f, 0f, 0f, 0f));

    //Explosion progress -> low-pass cutoff (Hz): muffled from the start (space), much more right after the core blast
    //(0.55), then the hearing slowly comes back while the light fades
    private static AnimationCurve RecommendedMuffleCutoff() => new AnimationCurve(
        new Keyframe(0f, 1400f, 0f, 0f), new Keyframe(0.55f, 1400f, 0f, 0f), new Keyframe(0.575f, 380f, 0f, 0f),
        new Keyframe(0.85f, 700f, 0f, 0f), new Keyframe(1f, 1400f, 0f, 0f));

    //Explosion progress -> whine (fraction of whineVolume): appears with the charging rays (0.10) and grows faster and
    //faster with them, loudest in the silence before the core blast (0.52 - 0.55), where it is the only sound left,
    //then fades out under the boom (gone at 0.62): no high note during the blasts
    private static AnimationCurve RecommendedWhineEnvelope() => new AnimationCurve(
        new Keyframe(0f, 0f, 0f, 0f), new Keyframe(0.10f, 0f, 0f, 0f), new Keyframe(0.35f, 0.25f, 1.8f, 1.8f),
        new Keyframe(0.55f, 1f, 5f, 0f), new Keyframe(0.62f, 0f, 0f, 0f));

    //Explosion progress -> whine pitch: from 75% to 100% of whineFrequency while the rays charge, faster near the end
    private static AnimationCurve RecommendedWhinePitch() => new AnimationCurve(
        new Keyframe(0.10f, 0.75f, 0f, 0.2f), new Keyframe(0.55f, 1f, 1.2f, 0f));

    //rising with the tension until the screen is covered, then sinking while it fades
    private static AnimationCurve RecommendedRumblePitch() => new AnimationCurve(
        new Keyframe(0f, 0.6f, 0.36f, 0.36f), new Keyframe(0.55f, 0.8f, 0.6f, 0.6f), new Keyframe(0.74f, 1.05f, 0f, 0f),
        new Keyframe(0.84f, 1f, -1.15f, -1.15f), new Keyframe(1f, 0.75f, -1.5f, -1.5f));

    private static AudioClip LoadClip(string assetPath)
    {
#if UNITY_EDITOR
        AudioClip clip = UnityEditor.AssetDatabase.LoadAssetAtPath<AudioClip>(assetPath);
        if (clip == null)
            Debug.LogWarning($"[LightExplosion] No audio clip at {assetPath}: assign the blast's sound by hand.");
        return clip;
#else
        return null;
#endif
    }
}
