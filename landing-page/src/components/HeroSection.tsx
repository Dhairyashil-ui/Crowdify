import React, { useRef, useEffect } from 'react';
import { Globe, ArrowRight } from 'lucide-react';

const InstagramIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="2" width="20" height="20" rx="5" ry="5"></rect>
    <path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"></path>
    <line x1="17.5" y1="6.5" x2="17.51" y2="6.5"></line>
  </svg>
);

const TwitterIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 4s-.7 2.1-2 3.4c1.6 10-9.4 17.3-18 11.6 2.2.1 4.4-.6 6-2C3 15.5.5 9.6 3 5c2.2 2.6 5.6 4.1 9 4-.9-4.2 4-6.6 7-3.8 1.1 0 3-1.2 3-1.2z"></path>
  </svg>
);

export default function HeroSection() {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    let fadeAnimation: number;
    let fadeStartTime: number | null = null;
    let fadingOut = false;
    let fadingIn = false;

    const animateFadeIn = (timestamp: number) => {
      if (!fadeStartTime) fadeStartTime = timestamp;
      const progress = timestamp - fadeStartTime;
      const newOpacity = Math.min(progress / 500, 1);
      video.style.opacity = newOpacity.toString();

      if (progress < 500) {
        fadeAnimation = requestAnimationFrame(animateFadeIn);
      } else {
        fadingIn = false;
        fadeStartTime = null;
      }
    };

    const animateFadeOut = (timestamp: number) => {
      if (!fadeStartTime) fadeStartTime = timestamp;
      const progress = timestamp - fadeStartTime;
      const currentOpacity = parseFloat(video.style.opacity) || 1;
      const newOpacity = Math.max(currentOpacity - (progress / 500), 0);
      video.style.opacity = newOpacity.toString();

      if (progress < 500) {
        fadeAnimation = requestAnimationFrame(animateFadeOut);
      } else {
        fadingOut = false;
        fadeStartTime = null;
      }
    };

    const handleCanPlay = () => {
      if (!fadingIn) {
        fadingIn = true;
        fadeStartTime = null;
        video.play().catch(console.error);
        cancelAnimationFrame(fadeAnimation);
        fadeAnimation = requestAnimationFrame(animateFadeIn);
      }
    };

    const handleTimeUpdate = () => {
      if (!video.duration) return;
      const remainingTime = video.duration - video.currentTime;
      if (remainingTime <= 0.55 && !fadingOut && remainingTime > 0) {
        fadingOut = true;
        fadeStartTime = null;
        cancelAnimationFrame(fadeAnimation);
        fadeAnimation = requestAnimationFrame(animateFadeOut);
      }
    };

    const handleEnded = () => {
      video.style.opacity = '0';
      setTimeout(() => {
        video.currentTime = 0;
        fadingOut = false;
        fadingIn = true;
        fadeStartTime = null;
        video.play().catch(console.error);
        cancelAnimationFrame(fadeAnimation);
        fadeAnimation = requestAnimationFrame(animateFadeIn);
      }, 100);
    };

    video.addEventListener('canplay', handleCanPlay);
    video.addEventListener('timeupdate', handleTimeUpdate);
    video.addEventListener('ended', handleEnded);

    return () => {
      video.removeEventListener('canplay', handleCanPlay);
      video.removeEventListener('timeupdate', handleTimeUpdate);
      video.removeEventListener('ended', handleEnded);
      cancelAnimationFrame(fadeAnimation);
    };
  }, []);

  return (
    <section className="min-h-screen overflow-hidden relative flex flex-col bg-black">
      {/* Background Video */}
      <video
        ref={videoRef}
        src="https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260405_074625_a81f018a-956b-43fb-9aee-4d1508e30e6a.mp4"
        muted
        autoPlay
        playsInline
        preload="auto"
        className="absolute inset-0 w-full h-full object-cover object-bottom z-0"
        style={{ opacity: 0 }}
      />

      {/* Navbar */}
      <nav className="relative z-20 px-6 py-6 w-full">
        <div className="liquid-glass rounded-full max-w-5xl mx-auto px-6 py-3 flex justify-between items-center">
          <div className="flex items-center">
            <Globe className="text-white w-6 h-6 mr-2" />
            <span className="text-white font-semibold text-lg">Asme</span>
            
            <div className="hidden md:flex gap-8 ml-8">
              <a href="#" className="text-white/80 hover:text-white text-sm font-medium transition-colors">Features</a>
              <a href="#" className="text-white/80 hover:text-white text-sm font-medium transition-colors">Pricing</a>
              <a href="#" className="text-white/80 hover:text-white text-sm font-medium transition-colors">About</a>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button className="text-white text-sm font-medium hover:text-white/80 transition-colors">Sign Up</button>
            <button className="liquid-glass rounded-full px-6 py-2 text-white text-sm font-medium hover:bg-white/5 transition-colors">Login</button>
          </div>
        </div>
      </nav>

      {/* Hero Content */}
      <div className="relative z-10 flex-1 flex flex-col items-center justify-center px-6 py-12 text-center -translate-y-[10%] md:-translate-y-[20%]">
        <h1 className="text-7xl md:text-8xl lg:text-9xl text-white tracking-tight whitespace-nowrap font-serif mb-8 md:mb-12">
          Know it then <em className="italic">all</em>.
        </h1>
        
        <div className="max-w-xl w-full mb-8">
          <div className="liquid-glass rounded-full pl-6 pr-2 py-2 flex items-center gap-3">
            <input
              type="email"
              placeholder="Enter your email"
              className="flex-1 bg-transparent border-none outline-none text-white placeholder:text-white/40 text-base"
            />
            <button className="bg-white rounded-full p-3 text-black hover:bg-white/90 transition-colors flex-shrink-0">
              <ArrowRight className="w-5 h-5" />
            </button>
          </div>
        </div>

        <p className="text-white/70 text-sm leading-relaxed px-4 max-w-md mx-auto mb-10">
          Stay updated with the latest news and insights. Subscribe to our newsletter today and never miss out on exciting updates.
        </p>

        <button className="liquid-glass rounded-full px-8 py-3 text-white text-sm font-medium hover:bg-white/10 transition-colors">
          Read our manifesto
        </button>
      </div>

      {/* Social Icons Footer */}
      <div className="relative z-10 flex justify-center gap-4 pb-12 mt-auto">
        <button className="liquid-glass rounded-full p-4 text-white/80 hover:text-white hover:bg-white/5 transition-all">
          <InstagramIcon className="w-5 h-5" />
        </button>
        <button className="liquid-glass rounded-full p-4 text-white/80 hover:text-white hover:bg-white/5 transition-all">
          <TwitterIcon className="w-5 h-5" />
        </button>
        <button className="liquid-glass rounded-full p-4 text-white/80 hover:text-white hover:bg-white/5 transition-all">
          <Globe className="w-5 h-5" />
        </button>
      </div>
    </section>
  );
}
