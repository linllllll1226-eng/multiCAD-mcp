import win32com.client
import pythoncom
import json
import os
import sys

def fix_zwcad_view():
    pythoncom.CoInitialize()
    try:
        # Connect to ZWCAD
        print("Connecting to ZWCAD...")
        try:
            app = win32com.client.GetActiveObject("ZWCAD.Application")
        except Exception:
            print("Error: ZWCAD not running.")
            return

        doc = app.ActiveDocument
        print(f"Connected to document: {doc.Name}")

        # Set visual style to Realistic
        # -VSCURRENT R (Realistic)
        print("Setting visual style to Realistic...")
        doc.SendCommand("-VSCURRENT R\n")

        # Zoom Extents
        app.ZoomExtents()

        # Find Point Cloud and get bounding box
        point_cloud = None
        for entity in doc.ModelSpace:
            if "PointCloud" in entity.ObjectName:
                point_cloud = entity
                break

        if point_cloud:
            print(f"Found Point Cloud: {point_cloud.Handle}")
            min_pt, max_pt = point_cloud.GetBoundingBox()
            
            bbox = {
                "min": [min_pt[0], min_pt[1], min_pt[2]],
                "max": [max_pt[0], max_pt[1], max_pt[2]],
                "handle": point_cloud.Handle
            }
            
            output_path = r"C:\Users\fuego\Documents\multiCAD Exports\point_cloud_info.json"
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(bbox, f, indent=2)
            print(f"Saved bounding box to {output_path}")
        else:
            print("Point cloud entity not found in ModelSpace.")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        pythoncom.CoUninitialize()

if __name__ == "__main__":
    fix_zwcad_view()
