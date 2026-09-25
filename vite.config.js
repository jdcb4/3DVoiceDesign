import { defineConfig } from "vite";
export default defineConfig({
  root: "web",
  build: {
    outDir: "../voicedesign/static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three", "three/addons/controls/OrbitControls.js"],
        },
      },
    },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8743" } },
});
