import React from 'react';
import HeroSection from './components/HeroSection';
import AboutSection from './components/AboutSection';
import FeaturedVideoSection from './components/FeaturedVideoSection';
import PhilosophySection from './components/PhilosophySection';
import ServicesSection from './components/ServicesSection';

function App() {
  return (
    <div className="bg-black min-h-screen text-white font-sans selection:bg-white/30">
      <HeroSection />
      <AboutSection />
      <FeaturedVideoSection />
      <PhilosophySection />
      <ServicesSection />
    </div>
  );
}

export default App;
