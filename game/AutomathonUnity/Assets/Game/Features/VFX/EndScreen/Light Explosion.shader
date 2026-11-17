// Full-screen post effect for the URP Full Screen Pass Renderer Feature.
// End-of-game light explosion: blast rings, rays, large rays and flashes, driven over time by LightExplosion.cs.

Shader "Automathon/LightExplosion"
{
    Properties
    {

        [Header(Global)]
        _ExplosionEpicenterX ("Explosion Epicenter X - screen coordinates", Range(0,1)) = 0.5    
        _ExplosionEpicenterY ("Explosion Epicenter Y - screen coordinates", Range(0,1)) = 0.5

        _ActivateBlast ("Activate Blast - 1 yes, 0 no", int) = 0
        _ActivateRays ("Activate Rays - 1 yes, 0 no", int) = 0
        _ActivateLargeRays ("Activate Large Rays - 1 yes, 0 no", int) = 0
        _ActivateFlash ("Activate Flash - 1 yes, 0 no", int) = 0

        _BlastAdvancement ("BlastAdvancement", Range(0,1)) = 0
        _RaysAdvancement ("RaysAdvancement - debug only, already applied to the ray arrays", Range(0,1)) = 0
        _LargeRaysAdvancement ("LargeRaysAdvancement - debug only, already applied to the ray arrays", Range(0,1)) = 0
        _FlashAdvancement ("FlashAdvancement - brightness it gives to a black pixel", Range(0,1)) = 0
        _FlashColor ("Flash Color", Color) = (1,1,1,1)


        [Header(Blast)]
        _ExplosionRange ("Explosion Range - 1 = screen center to corner", Float) = 0.5
        _BlastRangeRelativeToCorner ("Range Relative To Farthest Corner - 1 yes, 0 no", int) = 0
        _RingWidth ("Ring Width - 1 = screen center to corner", Float) = 0.02
        _FadingSpeed ("Fading Speed inside Ring - energy/advancement percentage", Float) = 2
        _IntensityCenterRing ("Intensity Center Ring - energy", Float) = 1
        _IntensityBorderRing ("Intensity Border Ring - energy", Float) = 0.8
        _BlastHotColor ("Blast Hot Color - ring and fresh halo", Color) = (1,1,1,1)
        _BlastCoolColor ("Blast Cool Color - halo about to fade out", Color) = (1,0.4,0.15,1)
        _BlastDistortionStrength ("Blast Distortion Strength - image shift at the ring, 1 = screen center to corner", Float) = 0
        _BlastDistortionWidth ("Blast Distortion Width - 1 = screen center to corner", Float) = 0.05
        _BlastDistortionChromatic ("Blast Distortion Chromatic Split - 0 none", Range(0,1)) = 0
        _BlastProgress ("Blast Progress - time, before the radius curve", Range(0,1)) = 0
        _BlastIntensity ("Blast Intensity - multiplier, from the intensity curve", Range(0,1)) = 1

        _BlastInverseCurve ("Blast Inverse Curve", 2D) = "black" {}
        _BlastShape ("Blast Shape - over the angle: r = range multiplier, g = brightness multiplier", 2D) = "white" {}

        // Rays and large rays have no properties here anymore: they are tuned in each effect's raysSettings
        // (LightExplosion component), generated once in C# when the effect starts, and sent as arrays.
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" "RenderPipeline" = "UniversalPipeline" }
        ZWrite Off Cull Off
        Pass
        {
            Name "LightExplosion"
            ZTest Always

            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.core/Runtime/Utilities/Blit.hlsl"

            //Global
            float _ExplosionEpicenterX;        
            float _ExplosionEpicenterY; 
            float2 _ExplosionEpicenter; 

            int _ActivateBlast;
            int _ActivateRays;
            int _ActivateLargeRays;
            int _ActivateFlash;

            float _BlastAdvancement;
            float _RaysAdvancement;
            float _LargeRaysAdvancement;
            float _FlashAdvancement;
            float4 _FlashColor;

            #define MAX_RAYS 12 // must match LightExplosion.MaxRays

            // Rays and large rays: drawn once in C# when the effect starts, then their advancement-dependent part is
            // updated once per frame for the whole screen. The only per-pixel work left is testing the pixel against each ray.
            int _RaysCount;
            float _RaysLengthWeight;               // brightness: weight of the falloff along the ray vs across it
            float4 _RaysGeometry[MAX_RAYS];        // (direction.x, direction.y, current length, current half width)
            float4 _RaysLight[MAX_RAYS];           // rgb = the ray's linear color * its current intensity, flicker included

            int _LargeRaysCount;
            float _LargeRaysLengthWeight;
            float4 _LargeRaysGeometry[MAX_RAYS];
            float4 _LargeRaysLight[MAX_RAYS];

            //Blast
            float _ExplosionRange;
            int _BlastRangeRelativeToCorner;
            float _RingWidth;
            float _FadingSpeed; 
            float _IntensityCenterRing;
            float _IntensityBorderRing;
            float _BlastProgress;  // t, the blast's own progress (time)
            float _BlastIntensity; // multiplier of the whole blast at t
            float4 _BlastHotColor;
            float4 _BlastCoolColor;
            float _BlastDistortionStrength;
            float _BlastDistortionWidth;
            float _BlastDistortionChromatic;

            TEXTURE2D(_BlastInverseCurve);
            float4 _BlastInverseCurve_TexelSize; // Unity generated texture size: .z = texture width

            // Baked by LightExplosion.cs when a blast starts. White (1, 1) = circular blast.
            TEXTURE2D(_BlastShape);
            SAMPLER(sampler_BlastShape); // the texture's own sampler: wrap mode Repeat, so -PI and PI join smoothly

            // Every effect works in "explosion space": relative to the epicenter, with the same unit on both axes, and
            // 1 = distance from the center of the screen to a corner. Recomputed every frame from the actual screen size,
            // so lengths keep the same proportion of the screen whatever the resolution, window size or aspect ratio,
            // and circles stay round (raw uv stretches them into ellipses by the aspect ratio).
            float2 UvToExplosionScale()
            {
                return _ScreenParams.xy / (0.5 * length(_ScreenParams.xy));
            }

            float2 ToExplosionSpace(float2 uv)
            {
                return (uv - _ExplosionEpicenter) * UvToExplosionScale();
            }

            // From the epicenter to the farthest corner of the screen, in explosion space
            float FarthestCornerDistance()
            {
                return length(max(_ExplosionEpicenter, 1 - _ExplosionEpicenter) * UvToExplosionScale());
            }


            // Every effect adds light *energy* (per channel, unbounded), and the total is applied once here.
            // Each channel moves toward 1 as 1 - exp(-energy): identity at 0 energy, never brighter than white, and no hard
            // clipping. A warm light (more red than blue) saturates its red first, so as its energy rises it goes
            // orange -> yellow -> white like overexposed film, instead of the flat white of adding HSL luminosity.
            // It also tints dark pixels with the light's color (black + HSL luminosity could only give gray).
            // max(0, 1 - col): pixels already brighter than white (HDR) are left as they are instead of being darkened.
            float3 ApplyLight(float3 col, float3 energy)
            {
                return col + max(0, 1 - col) * (1 - exp(-energy));
            }

            float InverseBlastCurve(float y)
            {
                float size = _BlastInverseCurve_TexelSize.z;
                float u = (saturate(y) * (size - 1.0) + 0.5) / size;
                return SAMPLE_TEXTURE2D_LOD(_BlastInverseCurve, sampler_LinearClamp, float2(u, 0.5), 0).r;
            }

            // Progress (time) at which the ring's inner edge was at distance d: radius(t) - _RingWidth = d
            // <=> f(t) = (d + _RingWidth) / range. This is why the radius curve f has to be strictly increasing.
            float ProgressWhenInnerEdgeReached(float d, float range)
            {
                return InverseBlastCurve((d + _RingWidth) / range);
            }

            // (range multiplier, brightness multiplier) in the direction of p
            float2 BlastShape(float2 p)
            {
                float u = atan2(p.y, p.x) / (2 * PI) + 0.5; // angle -PI..PI -> 0..1, like the texels baked in C#
                return SAMPLE_TEXTURE2D_LOD(_BlastShape, sampler_BlastShape, float2(u, 0.5), 0).rg;
            }

            // The blast in the direction of a pixel, shared by its light and its distortion
            struct BlastRing
            {
                float d;      // distance from the epicenter to the pixel
                float range;  // range in this direction
                float radius; // current ring radius in this direction
                float2 shape; // (range multiplier, brightness multiplier) in this direction
            };

            // Non-circular blast: each direction behaves like a circular blast with its own range (and brightness).
            // The time curve f is shared, only scaled per direction, so the halo's inverse still works:
            // a pixel never changes direction, so it only ever sees its own direction's range.
            BlastRing GetBlastRing(float2 p)
            {
                BlastRing ring;
                ring.shape = BlastShape(p);
                ring.range = _ExplosionRange * ring.shape.r * (_BlastRangeRelativeToCorner ? FarthestCornerDistance() : 1);
                ring.d = length(p);
                ring.radius = _BlastAdvancement * ring.range;
                return ring;
            }

            // Shockwave refraction: around the ring the image bulges outward, as seen through a lens of compressed air.
            // Returns where to read the scene from, as an offset in explosion space: toward the epicenter, so the content
            // there shows up further out.
            float2 BlastDistortion(float2 p, BlastRing ring)
            {
                if (_BlastDistortionStrength <= 0 || _BlastDistortionWidth <= 0 || ring.d < 1e-4)
                    return 0;

                float s = (ring.d - ring.radius) / _BlastDistortionWidth; // 0 on the ring, +-1 at the distortion's edges
                float bump = exp(-4 * s * s);
                return -(p / ring.d) * (_BlastDistortionStrength * _BlastIntensity * bump);
            }

            float3 BlastLight(BlastRing ring)
            {
                float d = ring.d;
                float range = ring.range;
                float2 shape = ring.shape;
                float ringRadius = ring.radius;
                float x = (d - ringRadius) / _RingWidth; // across the ring: -1 inner edge, 0 center, 1 outer edge

                float energy = 0;
                float cooling = 0; // 0 = hot color, 1 = cool color
                if (x < -1)
                {
                    // Halo: the inner edge left this pixel at progress tau, and the light has been fading since.
                    // At x = -1, tau = _BlastProgress so the halo starts exactly at the ring's border intensity.
                    float age = _BlastProgress - ProgressWhenInnerEdgeReached(d, range);
                    energy = _IntensityBorderRing - _FadingSpeed * age;
                    // the halo cools down while it fades, like cooling plasma: hot color when the ring has just passed,
                    // cool color when it goes out
                    cooling = saturate(age * _FadingSpeed / max(_IntensityBorderRing, 1e-4));
                }
                else if (x <= 0)
                {
                    // Inner side of the ring: border intensity -> center intensity
                    energy = lerp(_IntensityCenterRing, _IntensityBorderRing, smoothstep(0, 1, -x));
                }
                else if (x <= 1)
                {
                    // Outer side: center intensity -> 0, so the shockwave front has no hard edge
                    energy = _IntensityCenterRing * (1 - smoothstep(0, 1, x));
                }

                return max(0, energy) * shape.g * _BlastIntensity * lerp(_BlastHotColor.rgb, _BlastCoolColor.rgb, cooling);
            }
            
            // 0 outside the ray, up to 1 at its origin on its axis. p is the pixel relative to the epicenter.
            // Large rays skip the width test: each one lights the whole band [0, length] along its direction.
            float RayShape(float4 geometry, float2 p, float lengthWeight, bool isLargeRay)
            {
                float2 direction = geometry.xy;
                float rayLength = geometry.z;
                float halfWidth = max(geometry.w, 1e-6);
                if (rayLength <= 0) // not started yet (delay), also avoids dividing by 0
                    return 0;

                float along = dot(p, direction);
                float across = abs(dot(p, float2(-direction.y, direction.x)));
                if (along < 0 || along > rayLength || (!isLargeRay && across > halfWidth))
                    return 0;

                return lengthWeight * saturate(1 - along / rayLength) + (1 - lengthWeight) * saturate(1 - across / halfWidth);
            }

            // Sum of every ray's light on this pixel, each ray with its own color. isLargeRay is a literal at each call,
            // so the selects compile away.
            float3 RaysLight(float2 p, bool isLargeRay)
            {
                if (!(isLargeRay ? _ActivateLargeRays : _ActivateRays))
                    return 0;

                int count = isLargeRay ? _LargeRaysCount : _RaysCount;
                float lengthWeight = isLargeRay ? _LargeRaysLengthWeight : _RaysLengthWeight;

                float3 energy = 0;
                for (int i = 0; i < count; i++)
                {
                    float4 geometry = isLargeRay ? _LargeRaysGeometry[i] : _RaysGeometry[i];
                    float3 rayLight = isLargeRay ? _LargeRaysLight[i].rgb : _RaysLight[i].rgb;
                    energy += RayShape(geometry, p, lengthWeight, isLargeRay) * rayLight;
                }
                return energy;
            }

            // Uniform light over the whole screen: impact flash, and the final whiteout that hides the scene swap.
            // Its curve value v is the brightness it gives to a black pixel (as before), so it is turned into the energy
            // that does exactly that: 1 - exp(-energy) = v <=> energy = -ln(1 - v). v = 1 is capped to a finite energy that
            // is already white on screen (about 254/255 on the color's weakest channel).
            float3 FlashLight()
            {
                if (!_ActivateFlash)
                    return 0;

                float v = saturate(_FlashAdvancement); // saturate: a curve overshooting below 0 must not darken the screen
                return -log(max(1 - v, 1.0 / 4096)) * _FlashColor.rgb;
            }

            // Light alone only tends to white, and a colored flash never fully hides what is behind it. So near v = 1 the
            // flash also blends toward its own color, reaching it exactly at v = 1: the screen is then fully covered,
            // which is when the arena is swapped for the end menu. v^4 keeps small flashes (impact flash) pure light.
            float FlashCoverage()
            {
                if (!_ActivateFlash)
                    return 0;

                float v = saturate(_FlashAdvancement);
                return v * v * v * v;
            }


            // The scene, read uvOffset away. With an offset, red is bent a bit more than green and blue a bit less,
            // like through a prism. LOD 0: no derivatives needed, so sampling inside a branch is fine.
            float3 SampleScene(float2 uv, float2 uvOffset)
            {
                if (all(uvOffset == 0))
                    return SAMPLE_TEXTURE2D_X_LOD(_BlitTexture, sampler_LinearClamp, uv, 0).rgb;

                float r = SAMPLE_TEXTURE2D_X_LOD(_BlitTexture, sampler_LinearClamp, uv + uvOffset * (1 + _BlastDistortionChromatic), 0).r;
                float g = SAMPLE_TEXTURE2D_X_LOD(_BlitTexture, sampler_LinearClamp, uv + uvOffset, 0).g;
                float b = SAMPLE_TEXTURE2D_X_LOD(_BlitTexture, sampler_LinearClamp, uv + uvOffset * (1 - _BlastDistortionChromatic), 0).b;
                return float3(r, g, b);
            }

            half4 Frag (Varyings input) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);
                float2 uv = input.texcoord;

                _ExplosionEpicenter = float2(_ExplosionEpicenterX, _ExplosionEpicenterY);
                float2 p = ToExplosionSpace(uv);

                float3 blastLight = 0;
                float2 distortionUV = 0;
                if (_ActivateBlast)
                {
                    BlastRing ring = GetBlastRing(p);
                    blastLight = BlastLight(ring);
                    distortionUV = BlastDistortion(p, ring) / UvToExplosionScale(); // back from explosion space to uv
                }
                float3 col = SampleScene(uv, distortionUV);

                // all the light is summed as energy, then applied once: overlapping effects add up naturally
                float3 energy = RaysLight(p, false)
                              + RaysLight(p, true)
                              + blastLight
                              + FlashLight();
                col = ApplyLight(col, energy);
                col = lerp(col, _FlashColor.rgb, FlashCoverage());

                // --- General Explosion ---
                // Compute furthest distance from Epicenter
                //float distCorner1 = distance(float2(0, 0), _ExplosionEpicenter);
                //float distCorner2 = distance(float2(0, 1), _ExplosionEpicenter);
                //float distCorner3 = distance(float2(1, 0), _ExplosionEpicenter);
                //float distCorner4 = distance(float2(1, 1), _ExplosionEpicenter);
                //float furthest = max(max(distCorner1, distCorner2), max(distCorner3, distCorner4));
                //this being reached at _AdvancementForFullScreen advancement, compute the propagation speed (linear) and knowing the actual global advancement, compute the explosion range and local advancement
                //float propagationSpeed = furthest/_AdvancementForFullScreen;
                //float explosionRange = _ExplosionGlobalAdvancement * propagationSpeed;
                //float localRange = distance(_ExplosionEpicenter, uv);
                ///float localAdvancement;
                //if (localRange < explosionRange)
                //    localAdvancement = (explosionRange - localRange) / propagationSpeed;
                //else
                //    localAdvancement = 0;
                //final color
                //col = IncreaseLuminosity(col, localAdvancement * _BrigthnessIncreaseSpeed);
                return half4(col, 1);
            }
            ENDHLSL
        }
    }
}

