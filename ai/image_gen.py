import io
import os
import torch
import urllib.request
from diffusers import AutoPipelineForText2Image
from engine.config import config


class ImageGenerator:
    def __init__(self):
        self.model_id    = config.IMAGE_MODEL_NAME
        self.pipeline    = None
        self._fail_count = 0   # consecutive generation failures
        self._disabled   = False  # permanently off for this session after too many OOM

    # ------------------------------------------------------------------
    # Model switching
    # ------------------------------------------------------------------

    def switch_model(self, model_id):
        """Hot-swap the active image model. Unloads any loaded pipeline first."""
        if model_id != self.model_id:
            self.unload_model()
        self.model_id    = model_id
        self._fail_count = 0
        self._disabled   = False

    def _preset(self):
        return config.get_image_preset(self.model_id)

    def _provider(self):
        return self._preset().get('provider', 'diffusers')

    # ------------------------------------------------------------------
    # VRAM safety gate
    # ------------------------------------------------------------------

    def is_disabled(self):
        """True if auto-generation has been permanently disabled this session."""
        return self._disabled

    def can_generate_safely(self):
        """
        True if conditions allow an image generation attempt.
        Cloud providers (openai, stability) skip VRAM checks — they need only
        a valid API key, checked separately.
        """
        if self._disabled:
            return False
        provider = self._provider()
        if provider in ('openai', 'stability'):
            # Remote API — VRAM irrelevant; always attempt (key check is inside generate)
            return True
        # Local diffusers path
        if config.VRAM_STRATEGY == "A":
            return False
        if not torch.cuda.is_available():
            return False
        try:
            free_bytes = torch.cuda.mem_get_info()[0]
            free_gb    = free_bytes / (1024 ** 3)
            required   = getattr(config, 'IMAGE_VRAM_REQUIRED_GB', 4.0)
            return free_gb >= required
        except Exception:
            return True  # assume OK if mem_get_info not supported

    def reset_disabled(self):
        """Re-enable generation (e.g. user clicks Retry in sidebar)."""
        self._disabled   = False
        self._fail_count = 0

    # ------------------------------------------------------------------
    # Local diffusers lifecycle
    # ------------------------------------------------------------------

    def load_model(self):
        """Load diffusers pipeline into VRAM. No-op for cloud providers."""
        if self._provider() != 'diffusers':
            return
        if self.pipeline is None:
            print(f"[ImageGen] Loading {self.model_id} into VRAM…")
            preset = self._preset()
            # Some models don't ship a fp16 variant — fall back to standard load
            try:
                self.pipeline = AutoPipelineForText2Image.from_pretrained(
                    self.model_id, torch_dtype=torch.float16, variant="fp16"
                )
            except Exception:
                self.pipeline = AutoPipelineForText2Image.from_pretrained(
                    self.model_id, torch_dtype=torch.float16
                )
            self.pipeline.enable_model_cpu_offload()

    def unload_model(self):
        if self.pipeline is not None:
            print(f"[ImageGen] Unloading {self.model_id} from VRAM…")
            del self.pipeline
            torch.cuda.empty_cache()
            self.pipeline = None

    # ------------------------------------------------------------------
    # Main generation entry point — dispatches by provider
    # ------------------------------------------------------------------

    def generate_image(self, prompt, context_type="scene"):
        """
        Generate an image from prompt. Returns PIL Image or None.

        Dispatches to the correct backend based on the active model's provider:
          diffusers  — local GPU inference (Strategy A/B VRAM rules apply)
          openai     — DALL-E 3 via OpenAI REST API
          stability  — Stability AI Core via REST API
        """
        if self._disabled:
            return None

        provider = self._provider()
        if provider == 'openai':
            return self._generate_openai(prompt)
        elif provider == 'stability':
            return self._generate_stability(prompt)
        else:
            return self._generate_diffusers(prompt)

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    def _generate_diffusers(self, prompt):
        """Local GPU inference via HuggingFace diffusers."""
        if config.VRAM_STRATEGY == "A":
            return None

        preset   = self._preset()
        steps    = preset.get('steps', 2)
        guidance = preset.get('guidance', 0.0)

        if config.VRAM_STRATEGY == "B":
            try:
                self.load_model()
                image = self.pipeline(
                    prompt=prompt,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                ).images[0]
                self.unload_model()
                self._fail_count = 0
                return image

            except torch.cuda.OutOfMemoryError:
                self.unload_model()
                self._fail_count += 1
                max_f = getattr(config, 'IMAGE_GEN_MAX_FAILURES', 3)
                if self._fail_count >= max_f:
                    self._disabled = True
                    print(f"[ImageGen] OOM × {self._fail_count} — auto-generation disabled.")
                else:
                    print(f"[ImageGen] OOM (failure {self._fail_count}/{max_f})")
                return None

            except Exception as exc:
                self.unload_model()
                self._fail_count += 1
                max_f = getattr(config, 'IMAGE_GEN_MAX_FAILURES', 3)
                if self._fail_count >= max_f:
                    self._disabled = True
                    print(f"[ImageGen] {exc} — disabled after {self._fail_count} failures.")
                else:
                    print(f"[ImageGen] {exc} (failure {self._fail_count}/{max_f})")
                return None

        print(f"[ImageGen] Unknown VRAM strategy '{config.VRAM_STRATEGY}' — skipping.")
        return None

    def _generate_openai(self, prompt):
        """DALL-E 3 via OpenAI REST API. Returns PIL Image or None."""
        preset  = self._preset()
        env_key = preset.get('env_key', 'OPENAI_API_KEY')
        api_key = os.environ.get(env_key, '')
        if not api_key:
            print(f"[ImageGen/OpenAI] {env_key} not set — skipping.")
            return None
        try:
            import openai
            from PIL import Image
            client = openai.OpenAI(api_key=api_key)
            resp   = client.images.generate(
                model="dall-e-3",
                prompt=prompt[:1000],   # DALL-E 3 prompt length limit
                size="1024x1024",
                quality="standard",
                n=1,
            )
            url = resp.data[0].url
            with urllib.request.urlopen(url) as r:
                return Image.open(io.BytesIO(r.read())).copy()
        except Exception as e:
            print(f"[ImageGen/OpenAI] {e}")
            return None

    def _generate_stability(self, prompt):
        """Stability AI Core REST API. Returns PIL Image or None."""
        preset  = self._preset()
        env_key = preset.get('env_key', 'STABILITY_API_KEY')
        api_key = os.environ.get(env_key, '')
        if not api_key:
            print(f"[ImageGen/Stability] {env_key} not set — skipping.")
            return None
        try:
            import requests
            from PIL import Image
            response = requests.post(
                "https://api.stability.ai/v2beta/stable-image/generate/core",
                headers={
                    "authorization": f"Bearer {api_key}",
                    "accept":        "image/*",
                },
                files={"none": ''},
                data={"prompt": prompt[:2000], "output_format": "webp"},
                timeout=60,
            )
            if response.status_code == 200:
                return Image.open(io.BytesIO(response.content)).copy()
            print(f"[ImageGen/Stability] HTTP {response.status_code}: {response.text[:200]}")
            return None
        except Exception as e:
            print(f"[ImageGen/Stability] {e}")
            return None
