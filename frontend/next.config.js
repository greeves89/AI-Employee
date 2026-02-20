/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  webpack: (config) => {
    // react-pdf requires canvas alias to false (no native canvas in browser)
    config.resolve.alias.canvas = false;
    return config;
  },
};

module.exports = nextConfig;
