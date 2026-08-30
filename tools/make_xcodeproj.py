"""Generate swiftEngineSim.xcodeproj.

Written as a SCRIPT rather than committed as a generated blob, for the same
reason the fixtures are: a hand-edited pbxproj is a file nobody can review and
nobody dares regenerate.  This one can be deleted and rebuilt at any time, and
what it contains is legible here instead of in 400 lines of plist.

The project deliberately holds almost nothing:

  * ONE source file (App.swift) -- the whole UI lives in the package
  * a LOCAL package reference to swift/EngineSimCore, so the app tracks the
    library by path rather than by a copied snapshot
  * the three JSON fixtures, referenced IN PLACE under docs/ rather than
    copied, so regenerating them updates the app instead of leaving two
    copies to disagree
  * UIBackgroundModes = audio, without which the sound stops at screen lock,
    which in a car is most of the time

    py tools/make_xcodeproj.py

Then open swift/swiftEngineSimApp/swiftEngineSim.xcodeproj.
"""

from __future__ import annotations

import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ_DIR = os.path.join(ROOT, "swift", "swiftEngineSimApp",
                        "swiftEngineSim.xcodeproj")

APP = "swiftEngineSim"
BUNDLE_ID = "com.ziyuliu.swiftenginesim"
TEAM = "GHASXB9LZ2"          # from the Apple Development certificate on the Mac
DEPLOYMENT = "16.0"
RESOURCES = ["presets.json", "engine_voicing.json", "engine_tables.json",
             "engine_torque.json"]


def uid(name: str) -> str:
    """A stable 24-hex-character object id, derived from the name.

    Xcode only requires uniqueness, and deriving them means regenerating the
    project produces a byte-identical file rather than a diff of random ids.
    """
    return hashlib.sha1(("swiftEngineSim:" + name).encode()).hexdigest()[:24].upper()


def main():
    o = []           # (id, body) pairs, emitted in order

    def add(key, body):
        o.append((uid(key), body))
        return uid(key)

    # ---------------------------------------------------------- file refs
    app_ref = add("product", (
        '{isa = PBXFileReference; explicitFileType = wrapper.application; '
        'includeInIndex = 0; path = "%s.app"; sourceTree = BUILT_PRODUCTS_DIR; }'
        % APP))
    main_ref = add("App.swift", (
        '{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; '
        'path = App.swift; sourceTree = "<group>"; }'))
    res_refs = {}
    for r in RESOURCES:
        # ../../docs/<name>: referenced in place, NOT copied into the project
        res_refs[r] = add("res:" + r, (
            '{isa = PBXFileReference; lastKnownFileType = text.json; '
            'name = %s; path = ../../docs/%s; sourceTree = "<group>"; }' % (r, r)))

    # ------------------------------------------------------- package deps
    pkg_ref = add("localpkg", (
        '{isa = XCLocalSwiftPackageReference; relativePath = '
        '../EngineSimCore; }'))
    prod_core = add("prod:EngineSimCore", (
        '{isa = XCSwiftPackageProductDependency; productName = EngineSimCore; }'))
    prod_ui = add("prod:SwiftEngineSimUI", (
        '{isa = XCSwiftPackageProductDependency; productName = '
        'SwiftEngineSimUI; }'))

    # -------------------------------------------------------- build files
    bf_main = add("bf:App.swift",
                  '{isa = PBXBuildFile; fileRef = %s; }' % main_ref)
    bf_res = []
    for r in RESOURCES:
        bf_res.append(add("bf:" + r,
                          '{isa = PBXBuildFile; fileRef = %s; }' % res_refs[r]))
    bf_core = add("bf:EngineSimCore",
                  '{isa = PBXBuildFile; productRef = %s; }' % prod_core)
    bf_ui = add("bf:SwiftEngineSimUI",
                '{isa = PBXBuildFile; productRef = %s; }' % prod_ui)

    # ------------------------------------------------------------- groups
    grp_src = add("grp:src", (
        '{isa = PBXGroup; children = (%s, ); path = %s; sourceTree = '
        '"<group>"; }' % (main_ref, APP)))
    grp_res = add("grp:res", (
        '{isa = PBXGroup; children = (%s, ); name = "Engine data"; '
        'sourceTree = "<group>"; }'
        % ", ".join(res_refs[r] for r in RESOURCES)))
    grp_products = add("grp:products", (
        '{isa = PBXGroup; children = (%s, ); name = Products; sourceTree = '
        '"<group>"; }' % app_ref))
    grp_root = add("grp:root", (
        '{isa = PBXGroup; children = (%s, %s, %s, ); sourceTree = "<group>"; }'
        % (grp_src, grp_res, grp_products)))

    # ------------------------------------------------------ build phases
    ph_sources = add("ph:sources", (
        '{isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = '
        '(%s, ); runOnlyForDeploymentPostprocessing = 0; }' % bf_main))
    ph_frameworks = add("ph:frameworks", (
        '{isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = '
        '(%s, %s, ); runOnlyForDeploymentPostprocessing = 0; }'
        % (bf_core, bf_ui)))
    ph_resources = add("ph:resources", (
        '{isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = '
        '(%s, ); runOnlyForDeploymentPostprocessing = 0; }'
        % ", ".join(bf_res)))

    # ------------------------------------------------ build configurations
    common = [
        'ALWAYS_SEARCH_USER_PATHS = NO',
        'CLANG_ENABLE_OBJC_ARC = YES',
        'ENABLE_STRICT_OBJC_MSGSEND = YES',
        'GCC_NO_COMMON_BLOCKS = YES',
        'IPHONEOS_DEPLOYMENT_TARGET = %s' % DEPLOYMENT,
        'SDKROOT = iphoneos',
        'SWIFT_VERSION = 5.0',
        'TARGETED_DEVICE_FAMILY = "1,2"',
    ]
    target_common = [
        'ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon',
        'CODE_SIGN_STYLE = Automatic',
        'CURRENT_PROJECT_VERSION = 1',
        'DEVELOPMENT_TEAM = %s' % TEAM,
        'GENERATE_INFOPLIST_FILE = YES',
        # the one Info.plist key that matters: without it the audio stops the
        # moment the screen locks, and in a car the screen is locked
        'INFOPLIST_KEY_UIBackgroundModes = audio',
        'INFOPLIST_KEY_UILaunchScreen_Generation = YES',
        'INFOPLIST_KEY_UISupportedInterfaceOrientations = '
        '"UIInterfaceOrientationPortrait UIInterfaceOrientationLandscapeLeft '
        'UIInterfaceOrientationLandscapeRight"',
        'MARKETING_VERSION = 1.0',
        'PRODUCT_BUNDLE_IDENTIFIER = %s' % BUNDLE_ID,
        'PRODUCT_NAME = "$(TARGET_NAME)"',
        'SWIFT_EMIT_LOC_STRINGS = YES',
    ]

    def settings(lines):
        return "{ " + "; ".join(lines) + "; }"

    cfg_proj_debug = add("cfg:proj:debug", (
        '{isa = XCBuildConfiguration; buildSettings = %s; name = Debug; }'
        % settings(common + ['DEBUG_INFORMATION_FORMAT = dwarf',
                             'ENABLE_TESTABILITY = YES',
                             'GCC_OPTIMIZATION_LEVEL = 0',
                             'ONLY_ACTIVE_ARCH = YES',
                             'SWIFT_ACTIVE_COMPILATION_CONDITIONS = DEBUG',
                             'SWIFT_OPTIMIZATION_LEVEL = "-Onone"'])))
    cfg_proj_release = add("cfg:proj:release", (
        '{isa = XCBuildConfiguration; buildSettings = %s; name = Release; }'
        % settings(common + ['DEBUG_INFORMATION_FORMAT = '
                             '"dwarf-with-dsym"',
                             'ENABLE_NS_ASSERTIONS = NO',
                             'SWIFT_COMPILATION_MODE = wholemodule'])))
    cfg_t_debug = add("cfg:target:debug", (
        '{isa = XCBuildConfiguration; buildSettings = %s; name = Debug; }'
        % settings(target_common)))
    cfg_t_release = add("cfg:target:release", (
        '{isa = XCBuildConfiguration; buildSettings = %s; name = Release; }'
        % settings(target_common)))

    list_proj = add("list:proj", (
        '{isa = XCConfigurationList; buildConfigurations = (%s, %s, ); '
        'defaultConfigurationIsVisible = 0; defaultConfigurationName = '
        'Release; }' % (cfg_proj_debug, cfg_proj_release)))
    list_target = add("list:target", (
        '{isa = XCConfigurationList; buildConfigurations = (%s, %s, ); '
        'defaultConfigurationIsVisible = 0; defaultConfigurationName = '
        'Release; }' % (cfg_t_debug, cfg_t_release)))

    # ------------------------------------------------------ target, project
    target = add("target", (
        '{isa = PBXNativeTarget; buildConfigurationList = %s; buildPhases = '
        '(%s, %s, %s, ); buildRules = (); dependencies = (); name = %s; '
        'packageProductDependencies = (%s, %s, ); productName = %s; '
        'productReference = %s; productType = '
        '"com.apple.product-type.application"; }'
        % (list_target, ph_sources, ph_frameworks, ph_resources, APP,
           prod_core, prod_ui, APP, app_ref)))
    project = add("project", (
        '{isa = PBXProject; attributes = { BuildIndependentTargetsInParallel '
        '= 1; LastSwiftUpdateCheck = 1520; LastUpgradeCheck = 1520; '
        'TargetAttributes = { %s = { CreatedOnToolsVersion = 15.2; }; }; }; '
        'buildConfigurationList = %s; compatibilityVersion = "Xcode 14.0"; '
        'developmentRegion = en; hasScannedForEncodings = 0; knownRegions = '
        '(en, Base, ); mainGroup = %s; packageReferences = (%s, ); '
        'productRefGroup = %s; projectDirPath = ""; projectRoot = ""; '
        'targets = (%s, ); }'
        % (target, list_proj, grp_root, pkg_ref, grp_products, target)))

    # --------------------------------------------------------------- emit
    body = "\n".join("\t\t%s = %s;" % (i, b) for i, b in o)
    text = ("// !$*UTF8*$!\n{\n"
            "\tarchiveVersion = 1;\n\tclasses = {\n\t};\n"
            "\tobjectVersion = 56;\n\tobjects = {\n%s\n\t};\n"
            "\trootObject = %s;\n}\n" % (body, project))

    os.makedirs(PROJ_DIR, exist_ok=True)
    with open(os.path.join(PROJ_DIR, "project.pbxproj"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    # ---- the scheme, set to RUN IN RELEASE ---------------------------------
    # Xcode's default scheme runs Debug, and an unoptimised build of this chain
    # is roughly thirteen times slower -- enough to put a phone at 380 % of a
    # core and look like the port failed.  Shipping the scheme means pressing
    # Run gives the real thing, instead of the number being a trap that
    # everyone falls into once.
    schemes = os.path.join(PROJ_DIR, "xcshareddata", "xcschemes")
    os.makedirs(schemes, exist_ok=True)
    scheme = '''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion = "1520" version = "1.7">
   <BuildAction parallelizeBuildables = "YES" buildImplicitDependencies = "YES">
      <BuildActionEntries>
         <BuildActionEntry buildForTesting = "YES" buildForRunning = "YES"
                           buildForProfiling = "YES" buildForArchiving = "YES"
                           buildForAnalyzing = "YES">
            <BuildableReference
               BuildableIdentifier = "primary"
               BlueprintIdentifier = "%(target)s"
               BuildableName = "%(app)s.app"
               BlueprintName = "%(app)s"
               ReferencedContainer = "container:%(app)s.xcodeproj">
            </BuildableReference>
         </BuildActionEntry>
      </BuildActionEntries>
   </BuildAction>
   <TestAction buildConfiguration = "Debug"
      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"
      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"
      shouldUseLaunchSchemeArgsEnv = "YES">
   </TestAction>
   <LaunchAction buildConfiguration = "Release"
      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"
      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"
      launchStyle = "0" useCustomWorkingDirectory = "NO"
      ignoresPersistentStateOnLaunch = "NO" debugDocumentVersioning = "YES"
      debugServiceExtension = "internal" allowLocationSimulation = "YES">
      <BuildableProductRunnable runnableDebuggingMode = "0">
         <BuildableReference
            BuildableIdentifier = "primary"
            BlueprintIdentifier = "%(target)s"
            BuildableName = "%(app)s.app"
            BlueprintName = "%(app)s"
            ReferencedContainer = "container:%(app)s.xcodeproj">
         </BuildableReference>
      </BuildableProductRunnable>
   </LaunchAction>
   <ProfileAction buildConfiguration = "Release"
      shouldUseLaunchSchemeArgsEnv = "YES" savedToolIdentifier = ""
      useCustomWorkingDirectory = "NO" debugDocumentVersioning = "YES">
      <BuildableProductRunnable runnableDebuggingMode = "0">
         <BuildableReference
            BuildableIdentifier = "primary"
            BlueprintIdentifier = "%(target)s"
            BuildableName = "%(app)s.app"
            BlueprintName = "%(app)s"
            ReferencedContainer = "container:%(app)s.xcodeproj">
         </BuildableReference>
      </BuildableProductRunnable>
   </ProfileAction>
   <AnalyzeAction buildConfiguration = "Debug"></AnalyzeAction>
   <ArchiveAction buildConfiguration = "Release"
      revealArchiveInOrganizer = "YES"></ArchiveAction>
</Scheme>
''' % {"target": uid("target"), "app": APP}
    with open(os.path.join(schemes, APP + ".xcscheme"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(scheme)

    ws = os.path.join(PROJ_DIR, "project.xcworkspace")
    os.makedirs(ws, exist_ok=True)
    with open(os.path.join(ws, "contents.xcworkspacedata"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<Workspace version = "1.0">\n'
                 '   <FileRef location = "self:">\n'
                 '   </FileRef>\n'
                 '</Workspace>\n')
    print("wrote %s (%d objects)" % (PROJ_DIR, len(o)))
    print("  the scheme RUNS IN RELEASE -- a debug build of this chain is "
          "~13x slower")
    print("  open it, pick your phone, and run.")


if __name__ == "__main__":
    main()
