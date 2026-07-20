const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  webpack: (config) => {
    // react-pdf requires canvas alias to false (no native canvas in browser)
    config.resolve.alias.canvas = false;
    // Define the "@" path alias explicitly. Next's built-in tsconfig-paths
    // resolution was flaky here — it non-deterministically failed to resolve
    // "@/..." imports in some route modules (see issue #331). A hard webpack
    // alias resolves deterministically.
    config.resolve.alias["@"] = path.resolve(__dirname, "src");
    return config;
  },
};

module.exports = nextConfig;
