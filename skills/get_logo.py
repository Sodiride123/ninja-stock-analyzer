"""
Skill: get-logo
Fetches the company logo from the web and saves it as logo.jpeg
in the company's reports directory.

Strategy:
  1. Google Image Search for "{company} {ticker} stock logo icon"
  2. Score results by relevance (logo/brand/ticker in URL)
  3. Try downloading top candidates until one succeeds
  4. Resize to 512px max with PIL
  5. Fallback: generate a simple text-based logo
"""
import os
import re
import subprocess
import json
import requests
from skills.base import BaseSkill
from utils import logger, ensure_company_dir
from claude_wrapper import log_to_runlog


class GetLogoSkill(BaseSkill):
    name = "get-logo"
    description = "Fetch the company logo and save as logo.jpeg"

    # Known domain mappings for popular companies (used as search hint)
    DOMAIN_HINTS = {
        "AAPL": "apple.com", "MSFT": "microsoft.com", "GOOG": "google.com",
        "GOOGL": "google.com", "AMZN": "amazon.com", "META": "meta.com",
        "TSLA": "tesla.com", "NVDA": "nvidia.com", "NFLX": "netflix.com",
        "JPM": "jpmorganchase.com", "V": "visa.com", "MA": "mastercard.com",
        "DIS": "disney.com", "INTC": "intel.com", "AMD": "amd.com",
        "CRM": "salesforce.com", "ORCL": "oracle.com", "CSCO": "cisco.com",
        "ADBE": "adobe.com", "PYPL": "paypal.com", "BA": "boeing.com",
        "WMT": "walmart.com", "KO": "coca-cola.com", "PEP": "pepsico.com",
        "MCD": "mcdonalds.com", "NKE": "nike.com", "SBUX": "starbucks.com",
        "UNH": "unitedhealthgroup.com", "JNJ": "jnj.com", "PFE": "pfizer.com",
        "ABBV": "abbvie.com", "MRK": "merck.com", "LLY": "lilly.com",
        "TMO": "thermofisher.com", "ABT": "abbott.com", "BMY": "bms.com",
        "COST": "costco.com", "HD": "homedepot.com", "LOW": "lowes.com",
        "TGT": "target.com", "CVS": "cvshealth.com", "GS": "goldmansachs.com",
        "MS": "morganstanley.com", "BAC": "bankofamerica.com",
        "WFC": "wellsfargo.com", "C": "citigroup.com",
        "BRK.B": "berkshirehathaway.com", "XOM": "exxonmobil.com",
        "CVX": "chevron.com", "COP": "conocophillips.com",
    }

    def execute(self, ticker: str, company_name: str = None, **kwargs) -> dict:
        company_dir = ensure_company_dir(ticker)
        logo_path = os.path.join(company_dir, "logo.jpeg")

        # If logo already exists and is a real image (not a fallback), skip
        if os.path.exists(logo_path) and os.path.getsize(logo_path) > 5000:
            logger.info(f"Logo already exists for {ticker}: {logo_path}")
            return {"success": True, "logo_path": logo_path, "source": "cached"}

        log_to_runlog(f"Fetching logo for {ticker}...")

        # Strategy 1: Google Image Search
        logo_urls = self._search_google_images(ticker, company_name)
        for url in logo_urls:
            if self._download_and_convert(url, logo_path):
                log_to_runlog(f"Logo fetched via Google Images: {url[:80]}")
                return {"success": True, "logo_path": logo_path, "source": f"google:{url[:80]}"}

        # Strategy 2: Try Clearbit / Google favicon as fallback
        domain = self._get_domain(ticker, company_name)
        if domain:
            for api_url in [
                f"https://logo.clearbit.com/{domain}",
                f"https://www.google.com/s2/favicons?domain={domain}&sz=256",
            ]:
                if self._download_and_convert(api_url, logo_path, min_size=1500):
                    log_to_runlog(f"Logo fetched via API: {api_url}")
                    return {"success": True, "logo_path": logo_path, "source": f"api:{domain}"}

        # Strategy 3: Generate a simple text-based logo as fallback
        self._generate_fallback_logo(ticker, company_name, logo_path)
        log_to_runlog(f"Generated fallback text logo for {ticker}")
        return {"success": True, "logo_path": logo_path, "source": "generated_fallback"}

    def _search_google_images(self, ticker: str, company_name: str = None) -> list:
        """Search Google Images for the company logo and return ranked URLs."""
        name = company_name or ticker
        query = f"{name} {ticker} stock logo icon"

        try:
            url = f"https://www.google.com/search?q={requests.utils.quote(query)}&tbm=isch&tbs=isz:m"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Google Images returned {resp.status_code}")
                return []

            # Extract image URLs from the page
            img_urls = re.findall(r'https://[^"\'\\]+\.(?:png|jpg|jpeg|webp)', resp.text)

            # Score and rank URLs
            scored = []
            ticker_lower = ticker.lower()
            for u in img_urls:
                # Skip Google's own domains
                if any(d in u for d in ['google.com', 'gstatic.com', 'googleapis.com']):
                    continue
                score = 0
                ul = u.lower()
                if 'logo' in ul: score += 3
                if 'brand' in ul: score += 2
                if ticker_lower in ul: score += 2
                if 'companylogo' in ul: score += 3
                if 'icon' in ul: score += 1
                if 'symbol' in ul: score += 1
                # Penalize stock photo sites (watermarked images)
                if any(s in ul for s in ['shutterstock', 'alamy', 'dreamstime', 'istockphoto', 'gettyimages', 'depositphotos']):
                    score -= 10
                # Penalize news/article images
                if any(s in ul for s in ['imageio.forbes', 'specials-images', 'news/wp-content']):
                    score -= 5
                scored.append((score, u))

            scored.sort(key=lambda x: -x[0])

            # Deduplicate and return top candidates
            seen = set()
            result = []
            for s, u in scored:
                if s < 0:
                    continue
                if u not in seen:
                    seen.add(u)
                    result.append(u)
                if len(result) >= 8:
                    break

            logger.info(f"Google Images found {len(result)} logo candidates for {ticker}")
            return result

        except Exception as e:
            logger.warning(f"Google Image search failed for {ticker}: {e}")
            return []

    def _get_domain(self, ticker: str, company_name: str = None) -> str:
        """Get the company's domain name."""
        if ticker.upper() in self.DOMAIN_HINTS:
            return self.DOMAIN_HINTS[ticker.upper()]
        if company_name:
            name = company_name.lower()
            for suffix in [" inc", " inc.", " corp", " corp.", " co.", " ltd",
                           " llc", " plc", " group", " holdings", " technologies",
                           " technology", " systems", " international", " enterprises",
                           " & co", " company", ",", "."]:
                name = name.replace(suffix, "")
            name = name.strip().replace(" ", "")
            name = re.sub(r'[^a-z0-9]', '', name)
            if name:
                return f"{name}.com"
        return None

    def _download_and_convert(self, url: str, save_path: str, min_size: int = 3000) -> bool:
        """Download an image from URL, validate, and convert to JPEG."""
        temp_path = save_path + ".tmp"
        try:
            resp = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            })
            if resp.status_code != 200:
                return False

            # Check content type
            ct = resp.headers.get('Content-Type', '')
            if 'image' not in ct and 'octet-stream' not in ct:
                return False

            # Check size
            if len(resp.content) < min_size:
                return False

            # Save temp file
            with open(temp_path, 'wb') as f:
                f.write(resp.content)

            # Verify it's actually an image using file command
            result = subprocess.run(
                ["file", "--mime-type", temp_path],
                capture_output=True, text=True, timeout=10
            )
            if "image/" not in result.stdout:
                self._cleanup(temp_path)
                return False

            # Convert to JPEG using PIL
            self._convert_to_jpeg(temp_path, save_path)
            self._cleanup(temp_path)

            return os.path.exists(save_path) and os.path.getsize(save_path) > 500

        except Exception as e:
            logger.warning(f"Logo download failed from {url[:80]}: {e}")
            self._cleanup(temp_path)
            return False

    def _convert_to_jpeg(self, input_path: str, output_path: str):
        """Convert an image to JPEG, resize to max 512px, using PIL."""
        from PIL import Image
        img = Image.open(input_path)

        # Convert to RGB if necessary (handles PNG with alpha, etc.)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode in ('RGBA', 'LA'):
                background.paste(img, mask=img.split()[-1])
            else:
                background.paste(img)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Resize if too large (keep aspect ratio, max 512px)
        max_dim = 512
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        img.save(output_path, "JPEG", quality=90)

    def _generate_fallback_logo(self, ticker: str, company_name: str, save_path: str):
        """Generate a simple text-based logo as fallback."""
        try:
            from PIL import Image, ImageDraw, ImageFont

            display = ticker[:4].upper()
            size = 256
            img = Image.new('RGB', (size, size), '#1a1a2e')
            draw = ImageDraw.Draw(img)

            font_size = size // (len(display) + 1)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), display, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x = (size - tw) // 2
            y = (size - th) // 2

            margin = 20
            draw.ellipse([margin, margin, size - margin, size - margin], fill='#e94560')
            draw.text((x, y), display, fill='white', font=font)

            img.save(save_path, "JPEG", quality=90)
        except Exception as e:
            logger.warning(f"Fallback logo generation failed: {e}")

    def _cleanup(self, path):
        """Remove a temporary file if it exists."""
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass