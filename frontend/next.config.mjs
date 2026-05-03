/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a self-contained server bundle so the production Docker image
  // doesn't need to copy node_modules.
  output: "standalone",
};

export default nextConfig;
