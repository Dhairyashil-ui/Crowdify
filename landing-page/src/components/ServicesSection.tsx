import React, { useRef } from 'react';
import { motion, useInView } from 'framer-motion';
import { ArrowUpRight } from 'lucide-react';

export default function ServicesSection() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });

  const cards = [
    {
      video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260314_131748_f2ca2a28-fed7-44c8-b9a9-bd9acdd5ec31.mp4",
      tag: "Strategy",
      title: "Research & Insight",
      description: "We dig deep into data, culture, and human behavior to surface the insights that drive meaningful, lasting change."
    },
    {
      video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260324_151826_c7218672-6e92-402c-9e45-f1e0f454bdc4.mp4",
      tag: "Craft",
      title: "Design & Execution",
      description: "From concept to launch, we obsess over every detail to deliver experiences that feel effortless and look extraordinary."
    }
  ];

  return (
    <section className="bg-black py-28 md:py-40 px-6 overflow-hidden relative flex justify-center">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_rgba(255,255,255,0.02)_0%,_transparent_60%)] pointer-events-none" />
      
      <div className="max-w-6xl w-full relative z-10" ref={ref}>
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 30 }}
          transition={{ duration: 0.7 }}
          className="flex justify-between items-end mb-12"
        >
          <h2 className="text-3xl md:text-5xl text-white tracking-tight">What we do</h2>
          <span className="hidden md:block text-white/40 text-sm uppercase tracking-widest">Our services</span>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-8">
          {cards.map((card, idx) => (
            <motion.div
              key={idx}
              initial={{ opacity: 0, y: 50 }}
              animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 50 }}
              transition={{ duration: 0.8, delay: 0.15 * idx }}
              className="liquid-glass rounded-3xl overflow-hidden group flex flex-col"
            >
              <div className="relative aspect-video overflow-hidden">
                <video
                  src={card.video}
                  muted
                  autoPlay
                  loop
                  playsInline
                  preload="auto"
                  className="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/40 to-transparent pointer-events-none" />
              </div>
              
              <div className="p-6 md:p-8 flex flex-col flex-1 relative bg-black/20">
                <div className="flex justify-between items-start mb-12">
                  <span className="uppercase tracking-widest text-white/40 text-xs">{card.tag}</span>
                  <div className="liquid-glass rounded-full p-2 bg-white/5 group-hover:bg-white/20 transition-colors">
                    <ArrowUpRight className="text-white w-4 h-4" />
                  </div>
                </div>
                
                <div className="mt-auto">
                  <h3 className="text-white text-xl md:text-2xl mb-3 tracking-tight">{card.title}</h3>
                  <p className="text-white/50 text-sm leading-relaxed">{card.description}</p>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
