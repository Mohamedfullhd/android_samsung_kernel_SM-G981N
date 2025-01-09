sudo apt-get install clang-format clang-tidy clang-tools clang clangd libc++-dev libc++1 libc++abi-dev libc++abi1 libclang-dev libclang1 liblldb-dev libllvm-ocaml-dev libomp-dev libomp5 lld lldb llvm-dev llvm-runtime llvm python3-clang

sudo apt-get install gcc-aarch64-linux-gnu

git clone --depth=1 https://github.com/Mohamed4k/android_samsung_kernel_SM-G981N -b Test

cd android_samsung_kernel_SM-G981N

git clone https://gitlab.com/LeCmnGend/clang.git -b clang-18 --depth=1 clang-18


TC_DIR="$(pwd)/clang-18"

export PATH="$TC_DIR/bin:$PATH"
export CC=clang
export AR=llvm-ar
export NM=llvm-nm
export OBJCOPY=llvm-objcopy
export OBJDUMP=llvm-objdump
export STRIP=llvm-strip
export CROSS_COMPILE=aarch64-linux-gnu-
export CROSS_COMPILE_ARM32=arm-linux-gnueabi

mkdir -p out
make O=out ARCH=arm64 vendor/x1q_kor_singlex_defconfig

make -j$(nproc --all) O=out ARCH=arm64 CC=clang AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump STRIP=llvm-strip CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_ARM32=arm-linux-gnueabi- 2>&1 | tee log.txt
